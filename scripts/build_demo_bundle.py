"""
scripts/build_demo_bundle.py

Builds demo/, the data behind the web deployment (Vercel). It audits the generated contract set with the offline
reader into a fresh ledger, so the bundle is self-consistent and every proof can be replayed.

    python -m scripts.build_demo_bundle            # uses data/v2 (generated if missing); takes a few minutes

demo/audit_results.json   results shown in the web app
demo/contracts.json       contract text
demo/contract_meta.json   fictional customer, region and period (display only)
demo/ground_truth.json    scenario and expected outcome per contract (for the "benchmark check")
demo/telemetry.csv.gz     the fleets' telemetry, so the workbench can run the real pipeline on any contract
demo/benchmark.json       confusion matrix, per-scenario accuracy, baseline comparison and ablation
demo/ledger/              proofs and hash chain
"""
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core.ledger import Ledger  # noqa: E402
from core.pipeline import PipelineConfig, audit_contract  # noqa: E402

DATA = ROOT / "data" / "v2"
OUT = ROOT / "demo"
STATUSES = ["COMPLIANT", "BREACH", "ESCALATED"]


def ensure_data() -> None:
    if not (DATA / "contracts.json").exists():
        from data_gen.generate import generate
        DATA.mkdir(parents=True, exist_ok=True)
        contracts, df, truths = generate(200, seed=7)
        (DATA / "contracts.json").write_text(json.dumps(contracts, indent=1), encoding="utf-8")
        (DATA / "ground_truth.json").write_text(json.dumps(truths, indent=1), encoding="utf-8")
        df.to_csv(DATA / "telemetry_logs.csv", index=False)
    if not (DATA / "contract_meta.json").exists():
        from data_gen.make_meta import main as make_meta
        sys.argv = ["make_meta", "--data", str(DATA)]
        make_meta()


def benchmark(contracts, telemetry, truth, results) -> dict:
    from eval.ablation import VARIANTS, misread_numbers, run_variant, score
    from eval.run_eval import legacy_status

    by_id = {r["contract_id"]: r for r in results}
    rows = [{"id": c["contract_id"], "category": truth[c["contract_id"]]["category"],
             "expected": truth[c["contract_id"]]["expected_status"], "got": by_id[c["contract_id"]]["status"],
             "legacy": legacy_status(c, telemetry)} for c in contracts]

    def summary(key):
        n = len(rows)
        return {"accuracy": sum(r[key] == r["expected"] for r in rows) / n,
                "wrong_automatic": sum(r[key] != "ESCALATED" and r[key] != r["expected"] for r in rows),
                "over_escalated": sum(r[key] == "ESCALATED" and r["expected"] != "ESCALATED" for r in rows),
                "escalation_rate": sum(r[key] == "ESCALATED" for r in rows) / n}

    def confusion(key):
        grid = {e: {g: 0 for g in STATUSES} for e in STATUSES}
        for r in rows:
            grid[r["expected"]][r[key]] += 1
        return grid

    cats = defaultdict(list)
    for r in rows:
        cats[r["category"]].append(r)
    categories = [{"category": k, "n": len(v), "correct": sum(r["got"] == r["expected"] for r in v),
                   "wrong_automatic": sum(r["got"] != "ESCALATED" and r["got"] != r["expected"] for r in v),
                   "expected": v[0]["expected"]} for k, v in sorted(cats.items())]

    truths = {t["contract_id"]: t for t in truth.values()}
    ablation = []
    for name, ab in VARIANTS:
        n, acc, wrong, over, esc = score(run_variant(contracts, truths, telemetry, PipelineConfig(ablate=ab)))
        ablation.append({"variant": name, "accuracy": acc, "wrong_automatic": wrong, "over_escalated": over, "escalation_rate": esc})
    fault = []
    for name, ab in (("With quote verifier", frozenset()), ("Without quote verifier", frozenset({"verifier"}))):
        n, acc, wrong, over, esc = score(run_variant(contracts, truths, telemetry, PipelineConfig(ablate=ab, reader_hook=misread_numbers)))
        fault.append({"variant": name, "accuracy": acc, "wrong_automatic": wrong, "over_escalated": over, "escalation_rate": esc})

    return {"n": len(rows), "engine": summary("got"), "legacy": summary("legacy"),
            "confusion_engine": confusion("got"), "confusion_legacy": confusion("legacy"),
            "categories": categories, "ablation": ablation, "fault_injection": fault}


def main() -> None:
    ensure_data()
    contracts = json.loads((DATA / "contracts.json").read_text(encoding="utf-8"))
    telemetry = pd.read_csv(DATA / "telemetry_logs.csv")
    truth = {t["contract_id"]: t for t in json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))}

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    cfg = PipelineConfig(mode="mock", ledger=Ledger(OUT / "ledger"))
    results = [audit_contract(c, telemetry, cfg).model_dump() for c in contracts]

    (OUT / "audit_results.json").write_text(json.dumps(results), encoding="utf-8")
    shutil.copy(DATA / "contracts.json", OUT / "contracts.json")
    shutil.copy(DATA / "contract_meta.json", OUT / "contract_meta.json")
    (OUT / "ground_truth.json").write_text(json.dumps(
        {cid: {"category": t["category"], "expected_status": t["expected_status"]} for cid, t in truth.items()}), encoding="utf-8")
    telemetry.to_csv(OUT / "telemetry.csv.gz", index=False, compression="gzip")
    (OUT / "benchmark.json").write_text(json.dumps(benchmark(contracts, telemetry, truth, results), indent=1), encoding="utf-8")

    ok, msg = cfg.ledger.verify_chain()
    print(f"{len(results)} audits sealed; ledger: {msg}")
    assert ok


if __name__ == "__main__":
    main()
