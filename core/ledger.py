"""
core/ledger.py

Tamper-evident audit ledger. Each sealed decision is stored as a "proof bundle"
(everything needed to re-check it with no AI) and appended to a hash chain:
every entry contains the hash of the entry before it, so editing or deleting
any old record breaks every hash after it and `verify_chain()` reports where.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.proofs_dir = self.root / "proofs"
        self.chain_path = self.root / "ledger.jsonl"
        self._lock = threading.Lock()
        self.proofs_dir.mkdir(parents=True, exist_ok=True)

    # -- reading --------------------------------------------------------
    def entries(self) -> List[dict]:
        if not self.chain_path.exists():
            return []
        with open(self.chain_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def load_proof(self, proof_id: str) -> dict:
        path = self.proofs_dir / f"{proof_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"unknown proof id {proof_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    # -- writing --------------------------------------------------------
    def seal(self, bundle: dict, event: str = "audit_sealed", actor: str = "system:sla-harness") -> str:
        """Store the bundle and append a chained entry. Returns the proof id."""
        body = canonical(bundle)
        bundle_hash = sha256_text(body)
        proof_id = bundle_hash[:16]
        with self._lock:
            (self.proofs_dir / f"{proof_id}.json").write_text(body, encoding="utf-8")
            entries = self.entries()
            entry = {
                "seq": len(entries) + 1,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": event,
                "actor": actor,  # who made this decision: "system:..." (software) or "human:<name>"
                "contract_id": bundle.get("contract_id"),
                "proof_id": proof_id,
                "verdict": (bundle.get("decision") or {}).get("status"),
                "bundle_sha256": bundle_hash,
                "parent_proof_id": bundle.get("parent_proof_id"),
                "prev_hash": entries[-1]["hash"] if entries else GENESIS,
            }
            entry["hash"] = sha256_text(canonical(entry))
            with open(self.chain_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        return proof_id

    # -- checking -------------------------------------------------------
    def verify_chain(self) -> Tuple[bool, str]:
        prev = GENESIS
        for e in self.entries():
            body = {k: v for k, v in e.items() if k != "hash"}
            if e.get("prev_hash") != prev:
                return False, f"entry #{e.get('seq')}: chain link broken (an earlier record was changed or removed)"
            if sha256_text(canonical(body)) != e.get("hash"):
                return False, f"entry #{e.get('seq')}: entry contents were altered"
            proof_path = self.proofs_dir / f"{e['proof_id']}.json"
            if not proof_path.exists():
                return False, f"entry #{e.get('seq')}: proof bundle {e['proof_id']} is missing"
            if sha256_text(proof_path.read_text(encoding="utf-8")) != e["bundle_sha256"]:
                return False, f"entry #{e.get('seq')}: proof bundle {e['proof_id']} was altered"
            prev = e["hash"]
        return True, "ledger intact"

    def resolution_of(self, proof_id: str) -> Optional[dict]:
        """The human ruling already recorded for this escalation, if any."""
        return next((e for e in self.entries() if e.get("event") == "human_resolution" and e.get("parent_proof_id") == proof_id), None)

    def latest_for(self, contract_id: str) -> Optional[dict]:
        found = [e for e in self.entries() if e.get("contract_id") == contract_id]
        return found[-1] if found else None
