"""
agents/clause_reader.py

Clause Readers. Two independent readers look at the same contract and each
returns a ClauseExtraction (every candidate number WITH its quote):

  read_clauses_regex   deterministic, offline, no API key
  read_clauses_llm     AI reader with a self-healing retry loop; fails safe

Neither reader decides anything. `merge_extractions` takes the UNION of what
both found, so a disagreement between readers just becomes one more possible
reading for the envelope to test (core/envelope.py). Disagreement can only
add readings, never remove them, so it can only make the system more careful.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Any, List, Optional

from pydantic import ValidationError

from agents.number_words import find_numbers
from core.rules_ir import Candidate, ClauseExtraction
from core.schemas import CreditTier
from core.telemetry import record_ai_usage

PROMPT_VERSION = "clause-reader-v2"

_SENT_SPLIT = re.compile(r"(?<=[.;!?])\s+|\n+")
_RESOLUTION_KW = ("resolv", "resolution", "repair", "restor", "rectif", "fix")
_RESPONSE_KW = ("respond", "response", "acknowledg")
_NOISE_KW = ("terminat", "invoice", "payment", "renew", "insur", "liabil", "confidential", "indemn",
             "dispute", "arbitrat", "mediat", "negotiat")
_PRECEDENCE_RE = re.compile(r"supersede|prevail|take[s]? precedence|notwithstanding|shall control|override", re.I)
_VAGUE_PHRASES = (
    "timely, industry-standard", "industry-standard manner", "reasonable effort", "best efforts",
    "best-effort", "best-in-class", "reasonably possible", "prompt and consistent", "best practices",
    "promptly", "as soon as practicable", "reasonable time", "commercially reasonable", "optimal levels",
)
_PENALTY_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:per|for each|for every)\s+(?:whole\s+|full\s+)?(?:percentage point|percent|point)", re.I)
_PRECEDENCE_CONTEXT_RE = re.compile(r"conflict|inconsisten|notwithstanding|addendum|section \d|clause \d", re.I)
_SLA_WORDS = ("uptime", "availab", "operational", "fault", "repair", "restor", "resolv", "resolution", "respon", "critical", "vehicle")
# Credit-table rows. Row style: "Less than 99.99% but equal to or greater than 99.0% | 10%" and "Less than 95.0% | 100%".
_TIER_ROW = re.compile(
    r"less than\s*([\d.]+)\s*%(?:[^%\d]{0,40}?(?:equal to or greater than|greater than or equal to|at least|or more)\s*([\d.]+)\s*%)?[^%]*?([\d.]+)\s*%",
    re.I)
# Prose style: "a service credit of 10% ... if uptime falls below 99.9%"
_TIER_PROSE = re.compile(r"credit of\s*(\d+(?:\.\d+)?)\s*%.{0,90}?(?:less than|below|under)\s*([\d.]+)\s*%", re.I)
# Range style: "95.00% - < 99.00% (Premium and Standard Tier) | 25%" and "< 95.00% | 100%"
_TIER_RANGE = re.compile(r"^(?:([\d.]+)\s*%\s*[-–]\s*)?<\s*=?\s*([\d.]+)\s*%[^|]*\|\s*([\d.]+)\s*%\s*$")
# A table row that states a target: "Instances in Multiple Zones | >= 99.99%"
_TARGET_ROW = re.compile(r"\|\s*(?:>=|≥|at least)\s*([\d.]+)\s*%\s*$", re.I)
_NONCRIT_RE = re.compile(r"non[\s-]?critical", re.I)
_UNIT_AFTER = re.compile(r"^\s*-?\s*(business\s+)?(hours?|hrs?|days?)\b", re.I)
_PCT_AFTER = re.compile(r"^\s*(%|percent|per cent)", re.I)


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]


def _nearest_kind(sentence: str, pos: int) -> Optional[str]:
    """Does the verb governing the time at `pos` mean resolution or only first response?

    A time limit follows its verb ("respond ... within 1 hour", "restore ... within 12 hours"),
    so the closest keyword BEFORE the number wins; if there is none, the closest one after.
    """
    low = sentence.lower()
    before, after = (-1, None), (10**9, None)
    for kind, kws in (("resolution", _RESOLUTION_KW), ("response", _RESPONSE_KW)):
        for kw in kws:
            for m in re.finditer(kw, low):
                if m.start() < pos and m.start() > before[0]:
                    before = (m.start(), kind)
                elif m.start() >= pos and m.start() < after[0]:
                    after = (m.start(), kind)
    return before[1] or after[1]


def read_clauses_regex(item: dict) -> ClauseExtraction:
    cid = item.get("contract_id", "UNKNOWN")
    text = item.get("raw_text") or item.get("contract_text") or ""
    ext = ClauseExtraction(contract_id=cid)
    sentences = _sentences(text)
    crit_quote: Optional[str] = None
    # "entire agreement ... supersedes prior understandings" is boilerplate; a real precedence clause points at other clauses
    precedence_sentences = [s for s in sentences if _PRECEDENCE_RE.search(s) and _PRECEDENCE_CONTEXT_RE.search(s)]

    tier_rows: List[CreditTier] = []
    for si, s in enumerate(sentences):
        low = s.lower()
        if len(s) < 220 and ("|" in s or "credit" in low):
            tm = _TIER_ROW.search(s) if ("|" in s) else None
            if tm:
                top, bottom, pct = float(tm.group(1).rstrip(".")), tm.group(2), float(tm.group(3).rstrip("."))
                tier_rows.append(CreditTier(max_uptime=top, min_uptime=float(bottom.rstrip(".")) if bottom else None,
                                            credit_percent=pct, quote=s))
            elif _TIER_RANGE.match(s.strip()):
                rm = _TIER_RANGE.match(s.strip())
                tier_rows.append(CreditTier(max_uptime=float(rm.group(2).rstrip(".")), min_uptime=float(rm.group(1).rstrip(".")) if rm.group(1) else None,
                                            credit_percent=float(rm.group(3).rstrip(".")), quote=s))
            elif _TIER_PROSE.search(s):
                pm = _TIER_PROSE.search(s)
                tier_rows.append(CreditTier(max_uptime=float(pm.group(2).rstrip(".")), credit_percent=float(pm.group(1)), quote=s))
        uptime_noise = any(n in low for n in _NOISE_KW) or "credit" in low or "calculated" in low or "subtracting" in low
        row = _TARGET_ROW.search(s)
        if row and not uptime_noise and any(("uptime" in x.lower() or "availab" in x.lower()) for x in sentences[max(0, si - 14):si]):
            ext.uptime_candidates.append(Candidate(value=float(row.group(1).rstrip(".")), quote=s, governs=False))
        if not uptime_noise and any(k in low for k in ("uptime", "availab", "operational")):
            for value, start, end in find_numbers(s):
                if _PCT_AFTER.match(s[end:]) and 0 < value <= 100:
                    ext.uptime_candidates.append(Candidate(value=value, quote=s, governs=s in precedence_sentences))

        if any(n in low for n in _NOISE_KW) or _NONCRIT_RE.search(s):
            continue
        if any(k in low for k in _RESOLUTION_KW + _RESPONSE_KW):
            timed = []  # (value, unit match, kind) for every "<number> hours/days" in the sentence
            for value, start, end in find_numbers(s):
                m = _UNIT_AFTER.match(s[end:])
                if m:
                    timed.append((value, m, _nearest_kind(s, start)))
            # "respond within 1 hour and restore within 12 hours": keep only the repair time.
            # If the sentence does not separate the two, keep every number (more readings is safer).
            if any(kind == "resolution" for _, _, kind in timed):
                timed = [t for t in timed if t[2] == "resolution"]
            for value, m, _ in timed:
                if m.group(1):  # "2 business days" has no fixed hour length
                    ext.vague_terms.append(s)
                    continue
                hours = value * 24 if m.group(2).lower().startswith("day") else value
                ext.resolution_candidates.append(Candidate(value=hours, quote=s, governs=s in precedence_sentences))
                if re.search(r"critical", low):
                    crit_quote = s

        pm = _PENALTY_RE.search(s)
        if pm and ext.penalty_usd_per_point is None:
            ext.penalty_usd_per_point = float(pm.group(1).replace(",", ""))
            ext.penalty_quote = s

    table = 0
    for i, row in enumerate(tier_rows):
        if i and row.max_uptime is not None and tier_rows[i - 1].max_uptime is not None and row.max_uptime >= tier_rows[i - 1].max_uptime:
            table += 1  # a top tier after a lower one starts the next credit table
        row.table = table
    ext.credit_tiers = tier_rows

    if crit_quote:
        ext.scope_severities = ["critical"]
        ext.scope_quote = crit_quote
    if precedence_sentences:
        ext.precedence_quote = precedence_sentences[0]

    # Qualitative wording only matters where it describes an SLA term ("promptly notify" in boilerplate does not).
    for s in sentences:
        low = s.lower()
        states_number = any(s == c.quote for c in ext.uptime_candidates + ext.resolution_candidates)
        if any(p in low for p in _VAGUE_PHRASES) and any(k in low for k in _SLA_WORDS) and s not in ext.vague_terms and not states_number:
            ext.vague_terms.append(s)
    ext.vague_terms = ext.vague_terms[:4]
    return ext


# ---------------------------------------------------------------- AI reader

SYSTEM_PROMPT = f"""You are a Clause Reader for a commercial fleet-services SLA audit ({PROMPT_VERSION}).
You do NOT judge compliance. You only list what the contract says, with proof.

Return ONLY one JSON object with this shape:
{{
  "contract_id": string,
  "uptime_candidates":     [{{"value": number, "quote": string, "governs": boolean}}],
  "resolution_candidates": [{{"value": number, "quote": string, "governs": boolean}}],
  "scope_severities": ["critical", ...] | null,
  "scope_quote": string | null,
  "precedence_quote": string | null,
  "penalty_usd_per_point": number | null,
  "penalty_quote": string | null,
  "credit_tiers": [{{"min_uptime": number | null, "max_uptime": number | null, "credit_percent": number, "quote": string, "table": integer}}],
  "vague_terms": [string],
  "notes": [string]
}}

Rules:
1. List EVERY numeric uptime target (percent) and EVERY numeric repair/restoration time
   (convert days to hours; "value" is hours) that the contract states, including those in
   addenda, schedules and tables. Do not merge or pick between them.
2. "quote" must be the exact sentence copied from the contract that contains the number.
3. Set "governs": true on a candidate ONLY if the contract explicitly says which clause
   prevails and that clause is the one this value comes from; put that sentence in
   "precedence_quote". Never guess precedence.
4. "scope_severities": the event severities the repair-time clause covers (e.g. ["critical"]),
   or null if it covers every event.
5. If a requirement is described only in qualitative words (e.g. "timely", "reasonable
   effort", "optimal levels", "business days" without hours), do NOT invent a number: put
   the sentence in "vague_terms".
6. "penalty_usd_per_point": only if the contract states a dollar amount per percentage point
   of uptime shortfall; copy the sentence into "penalty_quote".
7. "credit_tiers": one entry per row of any service-credit table. A row means: uptime >= min_uptime
   and < max_uptime earns credit_percent of the monthly fee (null = unbounded). Rows of one table share the same
   "table" number; a second commitment with its own table gets the next number. Copy the row's wording into "quote".
8. The boundaries inside a credit table (for example 99.0% or 95.0% in "less than 99.9% but at least 99.0%") are NOT
   uptime targets. Only a commitment sentence ("will make the service available ... at least 99.9%") is a target.
9. A sentence such as "commercially reasonable efforts to make X available with an uptime of at least 99.9%" states a
   number, so it is NOT vague. Use "vague_terms" only for sentences with no number at all.
10. Ignore numbers unrelated to the uptime / repair-time SLA (payment terms, insurance, notice periods)."""

_TRUCK_RE = re.compile(r"\bTRK-[A-Z0-9]+\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def mask_text(text: str) -> str:
    """Strip direct identifiers before the text leaves the building (the AI never needs them)."""
    out = _EMAIL_RE.sub("[EMAIL]", text)
    out = _PHONE_RE.sub("[PHONE]", out)
    return out


def _build_user_prompt(item: dict, prior_error: Optional[str]) -> str:
    text = item.get("raw_text") or item.get("contract_text") or ""
    prompt = (
        f"contract_id: {item.get('contract_id', 'UNKNOWN')}\n\n"
        f"--- CONTRACT TEXT ---\n{mask_text(text)}\n--- END CONTRACT TEXT ---\n\n"
        "Return the JSON object."
    )
    if prior_error:
        prompt += f"\n\nYour previous answer was rejected:\n{prior_error}\nReturn a corrected JSON object."
    return prompt



# --- pacing and caching for the AI reader -------------------------------------------------
_pace_lock = threading.Lock()
_last_call = [0.0]


def _pace() -> None:
    """Keep at least SLA_AI_MIN_INTERVAL seconds between AI calls (free tiers limit tokens per minute)."""
    interval = float(os.getenv("SLA_AI_MIN_INTERVAL", "0") or 0)
    if interval <= 0:
        return
    with _pace_lock:
        wait = _last_call[0] + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _cache_path(item: dict) -> Optional[Path]:
    """Successful readings are cached on disk (SLA_AI_CACHE=<dir>) so an interrupted batch can resume."""
    root = os.getenv("SLA_AI_CACHE")
    if not root:
        return None
    from agents.extractor import _MODEL_NAME
    text = item.get("raw_text") or item.get("contract_text") or ""
    key = hashlib.sha256(f"{PROMPT_VERSION}|{_MODEL_NAME}|{mask_text(text)}".encode("utf-8")).hexdigest()[:24]
    return Path(root) / f"{key}.json"


def read_clauses_llm(item: dict, max_retries: int = 3, client: Optional[Any] = None) -> ClauseExtraction:
    """AI reader. Retries with the exact error fed back; on exhaustion returns extractor_failed=True."""
    from agents.extractor import _MODEL_NAME, _get_client

    cid = item.get("contract_id", "UNKNOWN")
    cached = _cache_path(item) if client is None else None
    if cached is not None and cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
        data["contract_id"] = cid
        return ClauseExtraction(**data)
    client = client or _get_client()
    last_error: Optional[str] = None
    for attempt in range(1, max_retries + 1):
        try:
            _pace()
            completion = client.chat.completions.create(
                model=_MODEL_NAME,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(item, last_error)},
                ],
                response_format={"type": "json_object"},
            )
            usage = getattr(completion, "usage", None)
            record_ai_usage(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0), retry=attempt > 1)
            data = json.loads(completion.choices[0].message.content or "")
            data["contract_id"] = cid
            ext = ClauseExtraction(**data)
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(ext.model_dump_json(), encoding="utf-8")
            return ext
        except (ValidationError, json.JSONDecodeError, ValueError, TypeError) as exc:
            last_error = f"Attempt {attempt}/{max_retries}: {exc}"
        except Exception as exc:  # noqa: BLE001 - network, rate limits, auth
            last_error = f"Attempt {attempt}/{max_retries}: {exc}\n{traceback.format_exc(limit=1)}"
            status = getattr(exc, "status_code", None)
            if status in (401, 403, 413):
                break  # a bad key or an oversized request will not fix itself; do not burn retries
            if status == 429:
                time.sleep(min(2 ** attempt * 8, 60))  # let the rate-limit window pass before retrying
    return ClauseExtraction(contract_id=cid, extractor_failed=True,
                            notes=[f"AI reader failed after {max_retries} attempts. Last error: {last_error}"])


# ---------------------------------------------------------------- merging

def _union(a: List[Candidate], b: List[Candidate]) -> List[Candidate]:
    out = {c.value: c for c in a}  # same value in both readers: keep the primary's quote and precedence call
    for c in b:
        out.setdefault(c.value, c)
    return list(out.values())


def _tier_key(tiers) -> list:
    """Order-independent fingerprint of a credit table (bounds may be None, so None sorts as -1)."""
    none = lambda v: -1.0 if v is None else v  # noqa: E731
    return sorted((none(t.min_uptime), none(t.max_uptime), t.credit_percent) for t in tiers)


def merge_extractions(primary: ClauseExtraction, secondary: ClauseExtraction) -> ClauseExtraction:
    """Union of two readers. `primary` wins on quotes and scope; scope disagreement is kept as options."""
    if primary.extractor_failed:
        return primary
    merged = primary.model_copy(deep=True)
    merged.uptime_candidates = _union(primary.uptime_candidates, secondary.uptime_candidates)
    merged.resolution_candidates = _union(primary.resolution_candidates, secondary.resolution_candidates)
    merged.vague_terms = list(dict.fromkeys(primary.vague_terms + secondary.vague_terms))[:4]
    merged.precedence_quote = primary.precedence_quote or secondary.precedence_quote
    p, s = sorted(primary.scope_severities or []), sorted(secondary.scope_severities or [])
    if p != s and merged.resolution_candidates:
        merged.scope_options = [primary.scope_severities, secondary.scope_severities]
        merged.notes.append(f"readers disagree on repair-time scope: {p or 'all events'} vs {s or 'all events'}")
    if primary.penalty_usd_per_point != secondary.penalty_usd_per_point:
        if primary.penalty_usd_per_point is not None and secondary.penalty_usd_per_point is not None:
            merged.penalty_usd_per_point, merged.penalty_quote = None, None
            merged.notes.append("readers disagree on the penalty rate; none used")
        elif primary.penalty_usd_per_point is None:
            merged.penalty_usd_per_point, merged.penalty_quote = secondary.penalty_usd_per_point, secondary.penalty_quote
    ptiers = _tier_key(primary.credit_tiers)
    stiers = _tier_key(secondary.credit_tiers)
    if not primary.credit_tiers:
        merged.credit_tiers = secondary.credit_tiers
    elif secondary.credit_tiers and ptiers != stiers:
        merged.credit_tiers = []
        merged.notes.append("readers disagree on the credit table; no tiered amount used")
    if sorted(c.value for c in primary.uptime_candidates) != sorted(c.value for c in secondary.uptime_candidates) or \
            sorted(c.value for c in primary.resolution_candidates) != sorted(c.value for c in secondary.resolution_candidates):
        merged.notes.append("readers found different numbers; all of them are tested as separate readings")
    return merged


def check_ai_access() -> tuple:
    """One tiny call to prove the key, endpoint and model work BEFORE a batch is started. Returns (ok, message)."""
    from agents.extractor import _MODEL_NAME, _get_client
    try:
        client = _get_client()
        client.chat.completions.create(model=_MODEL_NAME, max_tokens=5, temperature=0,
                                       messages=[{"role": "user", "content": "Reply with the word ok."}])
        return True, f"AI reader reachable (model {_MODEL_NAME})"
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "status_code", None)
        hint = " The API key is invalid or expired: put a working key in .env (OPENAI_API_KEY)." if code in (401, 403) else ""
        return False, f"AI reader NOT usable ({type(exc).__name__}: {str(exc)[:160]}).{hint}"
