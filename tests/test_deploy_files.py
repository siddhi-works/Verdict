"""The web deployment only works if every file it reads is actually committed (not git-ignored)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "api/index.py", "vercel.json", "requirements.txt", "public/index.html", "ui/users.json", "ui/users.py",
    "demo/audit_results.json", "demo/contracts.json", "demo/contract_meta.json", "demo/ledger/ledger.jsonl",
]


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout")
@pytest.mark.parametrize("rel", REQUIRED)
def test_deployment_file_exists_and_is_not_ignored(rel):
    assert (ROOT / rel).exists(), f"{rel} is missing"
    ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT).returncode == 0
    assert not ignored, f"{rel} is git-ignored, so it would not reach Vercel"


def test_every_proof_in_the_demo_ledger_is_present():
    import json
    chain = [json.loads(line) for line in (ROOT / "demo/ledger/ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    missing = [e["proof_id"] for e in chain if not (ROOT / "demo/ledger/proofs" / f"{e['proof_id']}.json").exists()]
    assert not missing
