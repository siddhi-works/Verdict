"""
scripts/build_demo_bundle.py

Builds demo/, the read-mostly data set behind the web deployment (Vercel). It audits the generated contract set
with the offline reader into a fresh ledger, so the bundle is self-consistent and every proof can be replayed.

    python -m scripts.build_demo_bundle            # uses data/v2 (generated if missing)

demo/audit_results.json   results shown in the web app
demo/contracts.json       contract text (for the cited-sentence view)
demo/contract_meta.json   fictional customer, region and period (display only)
demo/ledger/              proofs and hash chain (rulings made in the web app extend a temporary copy of this)
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core.ledger import Ledger  # noqa: E402
from core.pipeline import PipelineConfig, audit_contract  # noqa: E402

DATA = ROOT / "data" / "v2"
OUT = ROOT / "demo"


def main() -> None:
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

    contracts = json.loads((DATA / "contracts.json").read_text(encoding="utf-8"))
    telemetry = pd.read_csv(DATA / "telemetry_logs.csv")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    cfg = PipelineConfig(mode="mock", ledger=Ledger(OUT / "ledger"))
    results = [audit_contract(c, telemetry, cfg).model_dump() for c in contracts]

    (OUT / "audit_results.json").write_text(json.dumps(results), encoding="utf-8")
    shutil.copy(DATA / "contracts.json", OUT / "contracts.json")
    shutil.copy(DATA / "contract_meta.json", OUT / "contract_meta.json")
    ok, msg = cfg.ledger.verify_chain()
    print(f"{len(results)} audits sealed; ledger: {msg}")
    assert ok


if __name__ == "__main__":
    main()
