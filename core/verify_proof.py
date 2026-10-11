"""
core/verify_proof.py

Independent re-check of a sealed verdict. Uses NO AI and NO network: it takes
the proof bundle, the original contract text and the telemetry file, then
redoes the quote checks, the telemetry maths and the envelope decision from
scratch and compares the result with what was sealed.
"""
from __future__ import annotations

from typing import List, Tuple

import pandas as pd

from agents.clause_reader import mask_text
from agents.data_trust import build_views, contract_digest
from agents.quote_verifier import verify_extraction
from core.envelope import decide, resolve_decision, run_grid
from core.ledger import sha256_text
from core.rules_ir import ClauseExtraction, enumerate_readings


def verify_bundle(bundle: dict, text: str, df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """Return (all_ok, report lines)."""
    report: List[str] = []
    ok = True

    def check(passed: bool, label: str):
        nonlocal ok
        ok = ok and passed
        report.append(("PASS  " if passed else "FAIL  ") + label)

    cid = bundle["contract_id"]
    check(sha256_text(text) == bundle["contract_sha256"], "contract text matches the sealed hash")
    rows = df[df["contract_id"].astype(str) == str(cid)]
    check(contract_digest(rows) == bundle["telemetry_sha256"], "telemetry rows match the sealed hash")

    ext = ClauseExtraction(**bundle["extraction"])
    _, rejected = verify_extraction(ext, text, mask_text(text))
    check(not rejected, "every quoted sentence and number is present in the contract" + (f" ({rejected})" if rejected else ""))

    enum = enumerate_readings(ext)
    check(enum.model_dump() == bundle["enumeration"], "readings re-derived from the extraction are identical")

    views = {}

    def views_for(scope):
        key = "/".join(scope) if scope else "*"
        if key not in views:
            views[key] = build_views(cid, df, list(scope) if scope else None)
        return views[key]

    cells = run_grid(enum.readings, views_for) if enum.readings else []
    if not views:
        views_for(None)
    grades = [m.data_grade for v in views.values() for m in v.values()]
    check([c.model_dump() for c in cells] == bundle["cells"], "verdict grid recomputed from raw telemetry is identical")

    human = bundle.get("human")
    if human:
        decision = resolve_decision(enum, cells, grades, human["reading_id"], human.get("scenario"))
    else:
        decision = decide(enum, cells, grades)
    check(decision.model_dump() == bundle["decision"], f"final decision ({bundle['decision']['status']}) reproduced exactly")
    return ok, report
