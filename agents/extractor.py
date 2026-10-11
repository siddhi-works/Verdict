"""
agents/extractor.py

Legal Extractor Agent.

Uses an LLM to pull structured, numeric SLA terms out of a raw contract's
text and validates the response against the SLARules schema.

Two safety rules are enforced in the prompt:

  Rule 1 (Ambiguity): qualitative language instead of an explicit numeric
                       threshold -> ambiguity_detected = True
  Rule 2 (Conflict):  contradictory numeric thresholds across the same
                       contract / addenda -> conflict_detected = True

Self-healing retry loop: if the model's response fails JSON parsing or
Pydantic validation, the exact error is appended to the next prompt attempt
so the model can see what went wrong and correct itself, up to max_retries
times. If every attempt is exhausted, the function fails *safe*: it returns
an SLARules with ambiguity_detected=True rather than raising, so the
Gatekeeper automatically routes the contract to human escalation instead of
crashing the batch.

Offline / mock mode: set SLA_MOCK_MODE=true (or pass mock=True) to bypass
the LLM entirely and use a deterministic regex-based extractor instead. This
lets the full harness run with no network access and no API key — useful
for demos, CI, and sanity-checking the graph before spending API budget.
"""
from __future__ import annotations

import json
import os
import re
import traceback
from typing import Any, List, Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from core.schemas import SLARules

load_dotenv()

_MODEL_NAME = os.getenv("SLA_EXTRACTOR_MODEL", "gpt-4o-2024-08-06")

_client: Optional[Any] = None


def _get_client() -> Any:
    """Lazily instantiate the OpenAI-compatible client so importing this module has no side effects."""
    global _client
    if _client is None:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env and add a real key before running "
                "the extractor agent."
            )
        base_url = os.getenv("SLA_API_BASE_URL")  # e.g. Groq/OpenRouter endpoint
        if base_url:
            _client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            _client = OpenAI(api_key=api_key)
    return _client


SYSTEM_PROMPT = """You are a Legal Extractor Agent for a commercial fleet-services SLA audit.

Your ONLY job is to extract strict, numeric Service Level Agreement (SLA)
targets from the raw contract text you are given, and return them as JSON
matching this schema:

{
  "contract_id": string,
  "required_uptime_percent": number | null,
  "max_resolution_hours": integer | null,
  "conflict_detected": boolean,
  "ambiguity_detected": boolean,
  "ambiguity_notes": string | null
}

Rules you MUST follow:

1. AMBIGUITY: If the contract uses qualitative / vague phrasing instead of an
   explicit numeric target (e.g. "timely, industry-standard manner",
   "reasonable effort", "best-in-class response") for either the uptime or
   resolution-time requirement, set "ambiguity_detected" to true and explain
   which clause is vague in "ambiguity_notes". Do NOT invent a number to
   fill the gap — leave the corresponding field null.

2. CONFLICT: If the contract (including any addenda or amendment sections)
   states two different numeric values for what should be the same SLA
   term (e.g. one clause specifies a 6 hour resolution window and another
   specifies 12 hours), set "conflict_detected" to true and cite both
   conflicting values in "ambiguity_notes".

3. Only extract numbers that are explicitly written in the text. Never
   estimate, average, or infer a threshold that is not stated.

Return ONLY the JSON object — no prose, no markdown code fences.
"""


def _build_user_prompt(contract_item: dict, prior_error: Optional[str]) -> str:
    contract_id = contract_item.get("contract_id", "UNKNOWN")
    raw_text = (
        contract_item.get("raw_text")
        or contract_item.get("contract_text")
        or ""
    )

    prompt = (
        f"contract_id: {contract_id}\n\n"
        f"--- RAW CONTRACT TEXT ---\n{raw_text}\n--- END CONTRACT TEXT ---\n\n"
        "Extract the SLARules JSON object for this contract."
    )

    if prior_error:
        prompt += (
            "\n\nIMPORTANT: your previous response failed validation against the "
            "SLARules schema with the error below. Fix the problem and return a "
            f"corrected JSON object.\n\nVALIDATION ERROR:\n{prior_error}"
        )

    return prompt


# --------------------------------------------------------------------------
# Offline / mock extraction (no LLM call). Regex-based, deliberately
# conservative: it only ever fills in a number it can point to in the text,
# and defers everything else to the Gatekeeper's hard gates.
# --------------------------------------------------------------------------

AMBIGUOUS_PHRASES = (
    "timely, industry-standard",
    "industry-standard manner",
    "reasonable effort",
    "best efforts",
    "best-effort",
    "best-in-class",
    "reasonably possible",
    "prompt and consistent",
    "best practices",
)

_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_HOURS_RE = re.compile(r"(\d{1,3})[\s-]*hours?", re.IGNORECASE)
_RESOLUTION_KEYWORDS = ("resolv", "resolut", "respond", "response", "repair", "restor")


def _is_mock_mode() -> bool:
    return os.getenv("SLA_MOCK_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _find_uptime_candidates(text: str) -> List[float]:
    """Numeric percentages that appear within ~50 chars of the word 'uptime'."""
    candidates = []
    for m in _PERCENT_RE.finditer(text):
        window = text[max(0, m.start() - 50): m.end() + 50].lower()
        if "uptime" in window:
            candidates.append(float(m.group(1)))
    return candidates


def _find_resolution_candidates(text: str) -> List[int]:
    """Numeric hour values that appear near a resolution/response keyword."""
    candidates = []
    for m in _HOURS_RE.finditer(text):
        window = text[max(0, m.start() - 60): m.end() + 60].lower()
        if any(kw in window for kw in _RESOLUTION_KEYWORDS):
            candidates.append(int(m.group(1)))
    return candidates


def _mock_extract(contract_item: dict) -> SLARules:
    """Deterministic, offline stand-in for the LLM extraction call."""
    contract_id = contract_item.get("contract_id", "UNKNOWN")
    text = contract_item.get("raw_text") or contract_item.get("contract_text") or ""

    uptime_vals = sorted(set(_find_uptime_candidates(text)))
    hour_vals = sorted(set(_find_resolution_candidates(text)))

    conflict = False
    notes: List[str] = []

    if len(uptime_vals) > 1:
        conflict = True
        notes.append(f"Conflicting uptime targets found in text: {uptime_vals}%.")
    if len(hour_vals) > 1:
        conflict = True
        notes.append(f"Conflicting resolution windows found in text: {hour_vals} hours.")

    required_uptime = uptime_vals[0] if uptime_vals else None
    max_resolution = hour_vals[0] if hour_vals else None

    ambiguous = False
    if required_uptime is None or max_resolution is None:
        lowered = text.lower()
        hit = next((p for p in AMBIGUOUS_PHRASES if p in lowered), None)
        if hit:
            ambiguous = True
            notes.append(f'Qualitative phrasing instead of a numeric threshold: "{hit}".')

    return SLARules(
        contract_id=contract_id,
        required_uptime_percent=required_uptime,
        max_resolution_hours=max_resolution,
        conflict_detected=conflict,
        ambiguity_detected=ambiguous,
        ambiguity_notes="; ".join(notes) or None,
    )


def _parse_with_beta(client: Any, contract_id: str, user_prompt: str) -> SLARules:
    """Preferred path: openai>=1.40's structured-output helper."""
    completion = client.beta.chat.completions.parse(
        model=_MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format=SLARules,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        refusal = completion.choices[0].message.refusal
        raise ValueError(f"Model did not return a parseable SLARules object: {refusal}")
    if parsed.contract_id != contract_id:
        parsed = parsed.model_copy(update={"contract_id": contract_id})
    return parsed


def _parse_with_json_mode(client: Any, contract_id: str, user_prompt: str) -> SLARules:
    """Fallback for openai SDKs older than 1.40 that lack .beta.chat.completions.parse()."""
    completion = client.chat.completions.create(
        model=_MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    raw_content = completion.choices[0].message.content or ""
    data = json.loads(raw_content)
    data["contract_id"] = contract_id
    return SLARules(**data)


def extract_sla_with_retry(
    contract_item: dict, max_retries: int = 3, mock: Optional[bool] = None
) -> SLARules:
    """
    Extract SLARules from a raw contract dict, self-healing on validation failure.

    On every failure (JSON decode error, schema validation error, or API
    error) the traceback is fed back into the next prompt attempt so the
    model can see exactly what went wrong. After max_retries exhausted
    attempts, fails *safe* into an ambiguity-flagged SLARules rather than
    raising, so the pipeline always routes to human escalation instead of
    crashing.

    If `mock` is True, or left as None with SLA_MOCK_MODE set truthy in the
    environment, this bypasses the LLM entirely and uses the deterministic
    regex-based extractor — no network call, no API key required.
    """
    contract_id = contract_item.get("contract_id", "UNKNOWN")

    if mock is None:
        mock = _is_mock_mode()
    if mock:
        return _mock_extract(contract_item)

    client = _get_client()

    last_error: Optional[str] = None

    for attempt in range(1, max_retries + 1):
        user_prompt = _build_user_prompt(contract_item, last_error)
        try:
            try:
                if os.getenv("SLA_API_BASE_URL"):
                    # Third-party OpenAI-compatible providers (Groq, OpenRouter, etc.)
                    # generally don't support the strict json_schema mode that
                    # .beta.chat.completions.parse() relies on, so skip straight
                    # to plain JSON mode when pointed at one of those.
                    return _parse_with_json_mode(client, contract_id, user_prompt)
                return _parse_with_beta(client, contract_id, user_prompt)
            except AttributeError:
                # Installed openai SDK predates .beta.chat.completions.parse
                return _parse_with_json_mode(client, contract_id, user_prompt)

        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            last_error = f"Attempt {attempt}/{max_retries} failed:\n{traceback.format_exc()}"
            if attempt == max_retries:
                return SLARules(
                    contract_id=contract_id,
                    ambiguity_detected=True,
                    ambiguity_notes=(
                        f"Extraction failed after {max_retries} self-healing attempts. "
                        f"Last error: {exc}"
                    ),
                )
            continue

        except Exception as exc:  # noqa: BLE001 - API/network errors, rate limits, etc.
            last_error = f"Attempt {attempt}/{max_retries} failed:\n{traceback.format_exc()}"
            if attempt == max_retries:
                return SLARules(
                    contract_id=contract_id,
                    ambiguity_detected=True,
                    ambiguity_notes=(
                        f"Extraction agent raised an unrecoverable error after "
                        f"{max_retries} attempts: {exc}"
                    ),
                )
            continue

    # Unreachable in practice (loop always returns), kept for type-checkers.
    return SLARules(
        contract_id=contract_id,
        ambiguity_detected=True,
        ambiguity_notes="Extraction loop exited unexpectedly without a result.",
    )