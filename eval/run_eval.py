"""
eval/run_eval.py

Scores the harness against ground_truth.json (written by data_gen/generate.py,
never visible to the system) and compares it with the old single-reading gate.

    python -m eval.run_eval --data data/v2 [--mode mock|llm] [--limit N]

Numbers that matter:
  false automation   the system issued COMPLIANT/BREACH when the correct outcome
                     was different (this is the dangerous error; target: 0)
  over-escalation    the system sent a clear-cut contract to a human
  escalation rate    share of contracts needing a human
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.extractor import _mock_extract  # noqa: E402
from agents.parser import parse_telemetry  # noqa: E402
from core.gatekeeper import evaluate_gate  # noqa: E402
from core.pipeline import PipelineConfig, audit_contract  # noqa: E402

STATUSES = ["COMPLIANT", "BREACH", "ESCALATED"]


def legacy_status(item: dict, df: pd.DataFrame) -> str:
    """The original design: one regex reading, whole-fleet maximum, escalate on any doubt."""
    return evaluate_gate(_mock_extract(item), parse_telemetry(item["contract_id"], df)).status


def score(rows: List[dict], key: str) -> Dict[str, float]:
    n = len(rows)
    correct = sum(r[key] == r["expected"] for r in rows)
    false_auto = sum(r[key] != "ESCALATED" and r[key] != r["expected"] for r in rows)
    over_esc = sum(r[key] == "ESCALATED" and r["expected"] != "ESCALATED" for r in rows)
    esc = sum(r[key] == "ESCALATED" for r in rows)
    return {"n": n, "accuracy": correct / n if n else 0.0, "false_automation": false_auto,
            "over_escalation": over_esc, "escalation_rate": esc / n if n else 0.0}


def confusion(rows: List[dict], key: str) -> str:
    counts = collections.Counter((r["expected"], r[key]) for r in rows)
    head = "expected \\ got".ljust(16) + "".join(s.rjust(11) for s in STATUSES)
    lines = [head]
    for e in STATUSES:
        lines.append(e.ljust(16) + "".join(str(counts[(e, g)]).rjust(11) for g in STATUSES))
    return "\n".join(lines)


def fmt(m: Dict[str, float]) -> str:
    return (f"n={m['n']:<4} accuracy={m['accuracy']:.1%}  false-automation={m['false_automation']:<3} "
            f"over-escalation={m['over_escalation']:<3} escalation-rate={m['escalation_rate']:.1%}")


def run(data_dir: Path, mode: str, limit: int | None, categories: list | None = None) -> dict:
    contracts = json.loads((data_dir / "contracts.json").read_text(encoding="utf-8"))
    truth = {t["contract_id"]: t for t in json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))}
    df = pd.read_csv(data_dir / "telemetry_logs.csv")
    if categories:  # stratified sample: the first contract of each named category
        picked, seen = [], set()
        for c in contracts:
            cat = truth[c["contract_id"]]["category"]
            if cat in categories and cat not in seen:
                seen.add(cat)
                picked.append(c)
        contracts = picked
    if limit:
        contracts = contracts[:limit]
    cfg = PipelineConfig(mode=mode)
    rows = []
    for item in contracts:
        t = truth[item["contract_id"]]
        res = audit_contract(item, df, cfg)
        pen_ok = None
        if res.status == "BREACH" and t["expected_penalty_usd"] is not None:
            pen_ok = abs(res.penalty_usd - t["expected_penalty_usd"]) < 1.0 and not res.needs_commercial_review
        rows.append({"id": item["contract_id"], "category": t["category"], "expected": t["expected_status"],
                     "new": res.status, "legacy": legacy_status(item, df), "hard": t["hard_for_regex"],
                     "penalty_ok": pen_ok, "reason": res.reason})
    return {"rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "v2"))
    ap.add_argument("--mode", default="mock", choices=["mock", "llm"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--categories", help="comma-separated categories; takes the first contract of each")
    ap.add_argument("--json", help="write full per-contract results here")
    a = ap.parse_args()
    if a.mode == "llm":
        from agents.clause_reader import check_ai_access
        ok, msg = check_ai_access()
        print(msg)
        if not ok:
            sys.exit(2)
    out = run(Path(a.data), a.mode, a.limit, a.categories.split(",") if a.categories else None)
    rows = out["rows"]
    print(f"\n=== Verdict vs ground truth ({a.mode} reader, {len(rows)} contracts) ===\n")
    print("Legacy single-reading gate :", fmt(score(rows, "legacy")))
    print("Envelope engine            :", fmt(score(rows, "new")))
    easy = [r for r in rows if not r["hard"]]
    hard = [r for r in rows if r["hard"]]
    print("\nSplit by difficulty for the regex reader:")
    print("  clear-cut   envelope :", fmt(score(easy, "new")))
    print("  hard (needs AI)  env :", fmt(score(hard, "new")) if hard else "n/a")
    pens = [r["penalty_ok"] for r in rows if r["penalty_ok"] is not None]
    if pens:
        print(f"\nPenalty amounts exactly right when the contract states a rate: {sum(pens)}/{len(pens)}")
    print("\nConfusion matrix, envelope engine:\n" + confusion(rows, "new"))
    print("\nConfusion matrix, legacy gate:\n" + confusion(rows, "legacy"))
    print("\nBy category (envelope accuracy | false-automation | over-escalation):")
    by = collections.defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    for cat, rs in sorted(by.items()):
        m = score(rs, "new")
        print(f"  {cat:<32} n={m['n']:<3} {m['accuracy']:>6.0%} | {m['false_automation']} | {m['over_escalation']}")
    wrong = [r for r in rows if r["new"] != r["expected"] and r["new"] != "ESCALATED"]
    if wrong:
        print("\nFALSE AUTOMATIONS (investigate):")
        for r in wrong[:15]:
            print(f"  {r['id']} {r['category']}: expected {r['expected']} got {r['new']} - {r['reason']}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
