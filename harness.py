"""
Verdict, the Agentic SLA Auditor: batch runner.

    python harness.py --mock                      offline run on the bundled 50-contract set
    python harness.py --mock --data data/v2       run on the generated 190-contract set
    python harness.py                             AI reader + regex reader (needs OPENAI_API_KEY in .env)
    python harness.py --replay <proof_id>         re-check a sealed verdict with no AI and no network
    python harness.py --verify-ledger             check the audit ledger's hash chain
    python harness.py --eval --data data/v2       score against ground_truth.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from core.ledger import Ledger  # noqa: E402
from core.outbox import LogConnector, dispatch  # noqa: E402
from core.pipeline import PipelineConfig, audit_contract, contract_text  # noqa: E402
from core.precedents import PrecedentStore  # noqa: E402
from core.schemas import AuditResult  # noqa: E402
from core.verify_proof import verify_bundle  # noqa: E402

RESULTS_PATH = BASE_DIR / "audit_results.json"
RUN_INFO_PATH = BASE_DIR / "audit_run.json"


def load_dataset(data_dir: Path):
    contracts = json.loads((data_dir / "contracts.json").read_text(encoding="utf-8"))
    return contracts, pd.read_csv(data_dir / "telemetry_logs.csv")


def process_contract(item: dict, df: pd.DataFrame, cfg: PipelineConfig) -> AuditResult:
    cid = item.get("contract_id", "UNKNOWN")
    try:
        return audit_contract(item, df, cfg)
    except Exception as e:  # noqa: BLE001 - one bad contract must never stop the batch
        return AuditResult(contract_id=cid, status="ESCALATED", reason=f"Hard Gate: pipeline failure - {e}",
                           execution_trace=[f"unhandled error: {type(e).__name__}"])


def run_batch(contracts: List[dict], df: pd.DataFrame, cfg: PipelineConfig, workers: int) -> List[AuditResult]:
    if workers <= 1:
        return [process_contract(i, df, cfg) for i in contracts]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda i: process_contract(i, df, cfg), contracts))


def replay(ledger: Ledger, proof_id: str, data_dir: Path) -> int:
    bundle = ledger.load_proof(proof_id)
    contracts, df = load_dataset(data_dir)
    item = next((c for c in contracts if c["contract_id"] == bundle["contract_id"]), None)
    if item is None:
        print(f"contract {bundle['contract_id']} not found in {data_dir}")
        return 2
    ok, report = verify_bundle(bundle, contract_text(item), df)
    print(f"Replay of proof {proof_id} (contract {bundle['contract_id']}, verdict {bundle['decision']['status']}), no AI used:")
    print("\n".join("  " + line for line in report))
    print("RESULT:", "verified" if ok else "MISMATCH")
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mock", action="store_true", help="offline: regex reader only, no API key needed")
    p.add_argument("--data", default=str(BASE_DIR), help="folder with contracts.json and telemetry_logs.csv")
    p.add_argument("--workers", type=int, default=None, help="parallel contracts (default 1 offline, 4 with the AI reader)")
    p.add_argument("--ledger-dir", default=None)
    p.add_argument("--replay", metavar="PROOF_ID")
    p.add_argument("--verify-ledger", action="store_true")
    p.add_argument("--eval", action="store_true")
    a = p.parse_args()

    data_dir = Path(a.data).resolve()
    ledger = Ledger(Path(a.ledger_dir) if a.ledger_dir else BASE_DIR / "ledger" / ("v1" if data_dir == BASE_DIR else data_dir.name))
    precedents = PrecedentStore(BASE_DIR / "ledger" / "precedents.json")

    if a.replay:
        sys.exit(replay(ledger, a.replay, data_dir))
    if a.verify_ledger:
        ok, msg = ledger.verify_chain()
        print(("OK: " if ok else "TAMPERED: ") + msg + f" ({len(ledger.entries())} entries)")
        sys.exit(0 if ok else 1)
    if a.eval:
        from eval.run_eval import main as eval_main
        sys.argv = ["run_eval", "--data", str(data_dir), "--mode", "mock" if a.mock else "llm"]
        return eval_main()

    mode = "mock" if a.mock else "llm"
    if mode == "llm":
        from agents.clause_reader import check_ai_access
        ok, msg = check_ai_access()
        print(msg)
        if not ok:
            sys.exit(2)
    contracts, df = load_dataset(data_dir)
    cfg = PipelineConfig(mode=mode, ledger=ledger, precedents=precedents)
    t0 = time.time()
    results = run_batch(contracts, df, cfg, a.workers or (1 if a.mock else 4))
    elapsed = time.time() - t0

    connector = LogConnector(BASE_DIR / "ledger" / "outbox.jsonl")
    approvals = sum(1 for r in results for act in dispatch(r, connector) if act.get("approval") == "required")

    RESULTS_PATH.write_text(json.dumps([r.model_dump() for r in results], indent=2), encoding="utf-8")
    counts = Counter(r.status for r in results)
    exposure = sum(r.penalty_usd for r in results if r.status == "BREACH")
    info = {"data_dir": str(data_dir), "mode": mode, "contracts": len(results), "counts": dict(counts),
            "auto_rate": round(1 - counts.get("ESCALATED", 0) / max(len(results), 1), 4),
            "penalty_total_usd": exposure, "approvals_needed": approvals, "seconds": round(elapsed, 2)}
    RUN_INFO_PATH.write_text(json.dumps(info, indent=2), encoding="utf-8")

    print(f"Processed {len(results)} contracts in {elapsed:.1f}s ({mode} reader, data: {data_dir.name}).")
    print(dict(counts))
    print(f"Automated without a human: {info['auto_rate']:.0%}   Penalties on automated breaches: ${exposure:,.2f}   "
          f"Actions awaiting approval: {approvals}")
    esc = [r for r in results if r.status == "ESCALATED"]
    if esc:
        print(f"\nFirst escalation brief ({esc[0].contract_id}):\n{esc[0].brief or esc[0].reason}")
    print(f"\nResults: {RESULTS_PATH}\nLedger:  {ledger.root}  (check with --verify-ledger, replay any verdict with --replay <proof_id>)")


if __name__ == "__main__":
    main()
