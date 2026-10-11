"""
eval/run_baseline.py

The comparison a reviewer will ask for: ONE prompt to the SAME model, no harness.

The model receives the full contract text plus a telemetry summary computed with ordinary code
(fleet uptime, slowest repair overall and for critical events, counts of faulty rows). That makes the
baseline GENEROUS: it never has to do arithmetic over raw rows. It must still read the contract and
decide COMPLIANT / BREACH / ESCALATED itself.

    python -m eval.run_baseline --data data/v2 --categories a,b,c [--json out.json]

Pace with SLA_AI_MIN_INTERVAL (seconds between calls) on a token-limited tier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.clause_reader import _pace, check_ai_access  # noqa: E402
from agents.data_trust import classify_rows  # noqa: E402

SYSTEM = """You audit commercial fleet service contracts against vehicle telemetry.
Decide ONE status:
- COMPLIANT: the vendor met every numeric SLA term.
- BREACH: the vendor missed at least one numeric SLA term.
- ESCALATED: a human must decide, because the contract is contradictory or vague about a term that matters,
  or the telemetry is too unreliable to judge.
Return ONLY JSON: {"status": "COMPLIANT|BREACH|ESCALATED", "penalty_usd": number or null, "reason": string}"""


def telemetry_summary(contract_id: str, df: pd.DataFrame) -> str:
    rows = df[df["contract_id"].astype(str) == contract_id]
    if rows.empty:
        return "No telemetry rows exist for this contract."
    c = classify_rows(rows)
    usable = c[c["_class"] != "unusable"]
    per_truck = usable.groupby(usable["truck_id"].astype(str))["_up"].mean() if "truck_id" in usable.columns else usable["_up"]
    crit = usable[usable["severity"].astype(str).str.lower() == "critical"]
    planned = usable[usable["_planned"]] if "_planned" in usable.columns else usable.iloc[0:0]
    lines = [
        f"rows: {len(rows)} ({int((c['_class'] == 'unusable').sum())} unusable: missing or impossible values; "
        f"{int((c['_class'] == 'suspect').sum())} suspect: sensor FAULT, duplicate, or flat 0% uptime)",
        f"vehicles: {per_truck.shape[0]}",
        f"fleet uptime, mean over vehicles (usable rows): {per_truck.mean():.3f}%" if len(per_truck) else "fleet uptime: n/a",
        f"slowest repair, all usable events: {usable['_res'].max():.1f} h" if usable["_res"].notna().any() else "slowest repair: n/a",
        f"slowest repair, critical events only: {crit['_res'].max():.1f} h" if crit["_res"].notna().any() else "slowest repair, critical: n/a",
        f"events flagged as planned maintenance: {len(planned)}",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "v2"))
    ap.add_argument("--categories", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()

    ok, msg = check_ai_access()
    print(msg)
    if not ok:
        sys.exit(2)
    from agents.extractor import _MODEL_NAME, _get_client
    client = _get_client()

    d = Path(a.data)
    contracts = json.loads((d / "contracts.json").read_text(encoding="utf-8"))
    truth = {t["contract_id"]: t for t in json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))}
    df = pd.read_csv(d / "telemetry_logs.csv")
    wanted, seen, picked = set(a.categories.split(",")), set(), []
    for c in contracts:
        cat = truth[c["contract_id"]]["category"]
        if cat in wanted and cat not in seen:
            seen.add(cat)
            picked.append(c)

    cache_dir = Path(os.getenv("SLA_AI_CACHE", "data/v2/ai_cache")) / "baseline"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for item in picked:
        cid = item["contract_id"]
        prompt = (f"CONTRACT {cid}\n{item['contract_text']}\n\nTELEMETRY SUMMARY\n{telemetry_summary(cid, df)}\n\n"
                  "Return the JSON verdict.")
        key = hashlib.sha256((_MODEL_NAME + SYSTEM + prompt).encode()).hexdigest()[:24]
        cp = cache_dir / f"{key}.json"
        if cp.exists():
            data = json.loads(cp.read_text(encoding="utf-8"))
        else:
            data = {"status": "ERROR", "reason": "no response"}
            for attempt in range(1, 6):
                try:
                    _pace()
                    r = client.chat.completions.create(
                        model=_MODEL_NAME, temperature=0, response_format={"type": "json_object"},
                        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
                    data = json.loads(r.choices[0].message.content or "{}")
                    cp.write_text(json.dumps(data), encoding="utf-8")
                    break
                except Exception as e:  # noqa: BLE001
                    code = getattr(e, "status_code", None)
                    data = {"status": "ERROR", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
                    if code in (401, 403, 413):
                        break
                    if code == 429:
                        time.sleep(min(2 ** attempt * 8, 60))
        status = str(data.get("status", "ERROR")).upper()
        t = truth[cid]
        out.append({"id": cid, "category": t["category"], "expected": t["expected_status"], "got": status,
                    "penalty": data.get("penalty_usd"), "expected_penalty": t["expected_penalty_usd"],
                    "reason": str(data.get("reason", ""))[:160]})
        mark = "OK " if status == t["expected_status"] else ("!! " if status in ("COMPLIANT", "BREACH") else "~~ ")
        print(f"{mark}{cid} {t['category']:<28} expected {t['expected_status']:<10} got {status:<10}", flush=True)

    n = len(out)
    correct = sum(r["got"] == r["expected"] for r in out)
    wrong_auto = sum(r["got"] in ("COMPLIANT", "BREACH") and r["got"] != r["expected"] for r in out)
    errors = sum(r["got"] == "ERROR" for r in out)
    print(f"\nSingle-prompt baseline: n={n} correct={correct} wrong-automatic={wrong_auto} errors={errors}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
