"""
core/telemetry.py

Run tracing. Every audit gets a trace id and a list of timed spans (one per pipeline stage), written to
`<ledger>/traces.jsonl` next to the audit ledger. Attribute names follow OpenTelemetry's style
(`gen_ai.*` for model calls) so the records can be exported to an OTel backend later.

Privacy: spans hold timings, counts and ids only. Contract text, quotes and telemetry values are never recorded.
The trace sits OUTSIDE the proof bundle on purpose: timings differ on every run, and a proof must be reproducible.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

# Approximate AI usage counters (exact when audits run one at a time).
_usage_lock = threading.Lock()
_usage: Dict[str, int] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "retries": 0}


def record_ai_usage(prompt_tokens: int = 0, completion_tokens: int = 0, retry: bool = False) -> None:
    with _usage_lock:
        _usage["calls"] += 1
        _usage["prompt_tokens"] += int(prompt_tokens or 0)
        _usage["completion_tokens"] += int(completion_tokens or 0)
        _usage["retries"] += 1 if retry else 0


def usage_snapshot() -> Dict[str, int]:
    with _usage_lock:
        return dict(_usage)


class Trace:
    def __init__(self, name: str = "sla.audit", **attrs):
        self.trace_id = uuid.uuid4().hex
        self.name = name
        self.attrs = dict(attrs)
        self.spans: List[dict] = []
        self._t0 = time.perf_counter()

    @contextmanager
    def span(self, name: str, **attrs):
        start = time.perf_counter()
        record = {"name": name, "start_ms": round((start - self._t0) * 1000, 2), "attrs": dict(attrs)}
        before = usage_snapshot()
        try:
            yield record["attrs"]
            record["status"] = "ok"
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            record["status"] = "error"
            record["attrs"]["error.type"] = type(exc).__name__
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
            after = usage_snapshot()
            if after["calls"] != before["calls"]:
                record["attrs"].update({
                    "gen_ai.calls": after["calls"] - before["calls"],
                    "gen_ai.usage.input_tokens": after["prompt_tokens"] - before["prompt_tokens"],
                    "gen_ai.usage.output_tokens": after["completion_tokens"] - before["completion_tokens"],
                    "gen_ai.retries": after["retries"] - before["retries"],
                })
            self.spans.append(record)

    def finish(self, **attrs) -> dict:
        self.attrs.update(attrs)
        return {"trace_id": self.trace_id, "name": self.name,
                "duration_ms": round((time.perf_counter() - self._t0) * 1000, 2),
                "attrs": self.attrs, "spans": self.spans}


def write_trace(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def read_traces(path: Path, limit: Optional[int] = None) -> List[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows[-limit:] if limit else rows
