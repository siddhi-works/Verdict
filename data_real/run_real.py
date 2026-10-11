"""
data_real/run_real.py

Runs REAL public SLA wording through the pipeline, paired with SIMULATED fleet telemetry at a
sweep of uptime levels (real contract text, synthetic telemetry and fee; there is no public
fleet telemetry with these fields). There are no ground-truth labels for these documents, so
this is a behaviour study, not an accuracy number: what does the engine read, what does it
automate, what does it hand to a human, and is any automated amount wrong?

    python -m data_real.fetch_docs          # once
    python -m data_real.run_real [--mode mock|llm] [--out data_real/RESULTS.md]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from core.pipeline import PipelineConfig, audit_contract  # noqa: E402
from data_gen.generate import Fleet  # noqa: E402

UPTIME_SWEEP = [99.995, 99.97, 99.7, 99.3, 97.0, 92.0]  # fleet-mean uptime percent for the simulated month
FLEET_SIZE = 20


def simulated_fleet(contract_id: str, mean_uptime: float, seed: int = 5) -> pd.DataFrame:
    fl = Fleet(random.Random(seed), np.random.default_rng(seed), contract_id, FLEET_SIZE)
    fl.build(mean_uptime=mean_uptime, crit_max=1.0, noncrit_max=0.0, crit_cap=1.0)
    df = fl.frame(1)
    df["actual_uptime_percent"] = df["actual_uptime_percent"].clip(upper=100)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="mock", choices=["mock", "llm"])
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.mode == "llm":
        from agents.clause_reader import check_ai_access
        ok, msg = check_ai_access()
        print(msg)
        if not ok:
            sys.exit(2)
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    lines = [f"# Real public SLA wording, simulated fleets ({a.mode} reader)", "",
             "Contract text is real and public. Telemetry and monthly fee are simulated. No ground truth exists, so this shows behaviour, not accuracy.", ""]
    for doc in manifest["documents"]:
        path = HERE / "cache" / f"{doc['id']}.txt"
        if not path.exists():
            lines.append(f"## {doc['title']}\n\nnot cached: run `python -m data_real.fetch_docs`\n")
            continue
        text = path.read_text(encoding="utf-8")
        item = {"contract_id": doc["id"], "contract_text": text, "sla_profile": doc.get("sla_profile"),
                "monthly_fee_usd": doc.get("monthly_fee_usd")}
        lines += [f"## {doc['title']}", f"{len(text):,} characters. Expected: {doc['expect']}.", "",
                  "| fleet uptime | verdict | credit / amount | why |", "|---|---|---|---|"]
        print(f"\n=== {doc['title']} ({len(text):,} chars)")
        for up in UPTIME_SWEEP:
            res = audit_contract(item, simulated_fleet(doc["id"], up), PipelineConfig(mode=a.mode))
            n_read = len((res.envelope or {}).get("readings", []))
            amount = ""
            if res.status == "BREACH":
                amount = f"${res.penalty_usd:,.2f}" + (" (needs review)" if res.needs_commercial_review else "")
            elif res.status == "ESCALATED" and res.exposure_max_usd:
                amount = f"${res.exposure_min_usd:,.0f} to ${res.exposure_max_usd:,.0f} at stake"
            why = res.reason.replace("Hard Gate: ", "")
            lines.append(f"| {up:g}% | {res.status} | {amount or '-'} | {why} ({n_read} reading(s)) |")
            print(f"  uptime {up:>7g}%  {res.status:<10} {amount:<28} {why[:70]}  [{n_read} readings]")
        lines.append("")
    if a.out:
        Path(a.out).write_text("\n".join(lines), encoding="utf-8")
        print(f"\nwritten: {a.out}")


if __name__ == "__main__":
    main()
