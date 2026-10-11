"""
eval/ablation.py

Switches off one part of the harness at a time and re-scores it against ground_truth.json, so each
design idea has a measured value. Offline (pattern reader), so it is fast and repeatable.

    python -m eval.ablation --data data/v2

Plus one fault-injection experiment for the quote verifier: a reader that silently MISREADS numbers
(simulating a hallucinating AI) is run with and without the verifier. This is a controlled stress
test, not a measurement of any real model's error rate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.pipeline import PipelineConfig, audit_contract  # noqa: E402

VARIANTS = [
    ("full system", frozenset()),
    ("- envelope (take the first reading only)", frozenset({"envelope"})),
    ("- data views (trust every parseable row)", frozenset({"data_views"})),
    ("- scope filter (check all events, not just critical)", frozenset({"scope"})),
    ("- precedence handling", frozenset({"precedence"})),
    ("- vague-wording handling", frozenset({"vague"})),
]


def misread_numbers(ext):
    """Fault injector: every number the reader reports is slightly wrong (looser repair time, lower uptime)."""
    return ext.model_copy(update={
        "resolution_candidates": [c.model_copy(update={"value": c.value * 1.5}) for c in ext.resolution_candidates],
        "uptime_candidates": [c.model_copy(update={"value": max(c.value - 1.0, 0.0)}) for c in ext.uptime_candidates],
    })


def score(rows):
    n = len(rows)
    ok = sum(r["got"] == r["expected"] for r in rows)
    wrong = sum(r["got"] != "ESCALATED" and r["got"] != r["expected"] for r in rows)
    over = sum(r["got"] == "ESCALATED" and r["expected"] != "ESCALATED" for r in rows)
    esc = sum(r["got"] == "ESCALATED" for r in rows)
    return n, ok / n, wrong, over, esc / n


def run_variant(contracts, truth, df, cfg):
    rows = []
    for item in contracts:
        res = audit_contract(item, df, cfg)
        rows.append({"id": item["contract_id"], "expected": truth[item["contract_id"]]["expected_status"], "got": res.status})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "v2"))
    ap.add_argument("--out")
    a = ap.parse_args()
    d = Path(a.data)
    contracts = json.loads((d / "contracts.json").read_text(encoding="utf-8"))
    truth = {t["contract_id"]: t for t in json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))}
    df = pd.read_csv(d / "telemetry_logs.csv")

    lines = ["| variant | accuracy | wrong automatic verdicts | over-escalations | escalation rate |", "|---|---|---|---|---|"]
    for name, ab in VARIANTS:
        n, acc, wrong, over, esc = score(run_variant(contracts, truth, df, PipelineConfig(ablate=ab)))
        lines.append(f"| {name} | {acc:.1%} | {wrong} | {over} | {esc:.1%} |")
        print(f"{name:<56} acc={acc:6.1%}  wrong-auto={wrong:<3} over-esc={over:<3} esc-rate={esc:.1%}", flush=True)

    print("\nFault injection: a reader that misreads every number")
    lines += ["", "Fault injection (reader misreads every number it reports):", "",
              "| variant | accuracy | wrong automatic verdicts | over-escalations | escalation rate |", "|---|---|---|---|---|"]
    for name, ab in (("with quote verifier", frozenset()), ("without quote verifier", frozenset({"verifier"}))):
        n, acc, wrong, over, esc = score(run_variant(contracts, truth, df, PipelineConfig(ablate=ab, reader_hook=misread_numbers)))
        lines.append(f"| {name} | {acc:.1%} | {wrong} | {over} | {esc:.1%} |")
        print(f"{name:<56} acc={acc:6.1%}  wrong-auto={wrong:<3} over-esc={over:<3} esc-rate={esc:.1%}", flush=True)

    if a.out:
        Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwritten: {a.out}")


if __name__ == "__main__":
    main()
