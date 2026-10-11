# Architecture

Two views: what is built and tested today, and a target deployment. **The target view is a design, not an implementation.**

## 1. As built

```
 contracts (.json)     telemetry (.csv)
        │                    │
        ▼                    ▼
  Clause readers  ──►  Quote verifier ──►  Reading enumerator
  (AI + pattern)       (+ reconcile)             │
                                                 ▼
                               Data trust (strict / inclusive views, grade A–D)
                                                 │
                                                 ▼
                          Verdict envelope: readings × data views → decision
                                                 │
              ┌──────────────────────────────────┼──────────────────────────┐
              ▼                                  ▼                          ▼
     Proof bundle + hash-chained ledger    Run trace (spans)        Outbox actions + brief
              │                                                            │
              ▼                                                            ▼
   replay (no AI)  ·  human review queue  ·  precedent memory     review ticket / invoice hold
```

| Concern | Where | Notes |
|---|---|---|
| Orchestration | `core/pipeline.py` | One function per audit; typed models between stages |
| AI boundary | `agents/clause_reader.py` | The only place a model is called; output is verified before use |
| Decision | `core/envelope.py`, `core/gatekeeper.py` | Deterministic; no model involved |
| Evidence / audit | `core/ledger.py`, `core/verify_proof.py` | SHA-256 hash chain; each entry records its actor (`system:` or `human:`) |
| Observability | `core/telemetry.py` | Per-audit trace and stage spans in `traces.jsonl`; OpenTelemetry-style attribute names; no contract text |
| Human oversight | review queue, `resolve_escalation` | One ruling per escalation; rulings become suggestions, never automatic |
| Access control | `api/main.py` | Token roles (analyst / reviewer / admin), per-tenant ledgers |
| Packaging | `Dockerfile`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml` | Container and CI files are written but **not yet run** by me |

## 2. Target deployment (design only, not built)

Michelin's published data platform is Azure with Databricks, with Azure Data Factory landing files in storage
([source](https://www.journaldunet.com/solutions/dsi/1505561-michelin-propulse-120-projets-sur-son-data-lake-en-deux-ans/));
ServiceNow appears in third-party technology profiles. This table is how the harness **would** plug in. Nothing below is implemented or tested.

| Harness part | Target |
|---|---|
| Telemetry input | CSV or Parquet landed by the existing data pipeline; the harness reads a path, so no code change is expected |
| Contract input | Document store or SharePoint export to text |
| Ledger and proofs | Postgres (append-only table, same hash chain) with files in object storage |
| Escalation action | `Connector` implementation that opens a ServiceNow incident from the brief |
| Invoice hold / release | `Connector` implementation for the ERP's hold API |
| Traces | Export the spans to an OpenTelemetry collector |
| AI endpoint | Any OpenAI-compatible endpoint, including a private deployment; identifiers are masked before the call |
| Jobs | Batch per month via the existing CLI under a scheduler; a queue only if volume demands it |

## 3. Deliberately not built

A message queue, a relational database, a cloud SDK, and live ticketing or chat integrations. The reason is scope: none of them
changes how a verdict is reached or proven, and each would need credentials and a system to test against. They are listed as target-state so the
boundary (`Connector`, the ledger interface, the path-based inputs) is visible.

## 4. Regulatory note

EU rules for high-risk AI systems stress automatic logging, traceability and meaningful human oversight
([overview](https://www.mishcon.com/eu-uk-ai-navigator/article-12)). The ledger, replay and review queue are designed in that spirit.
This is **not** a compliance claim: whether this system falls in scope, and what that would require, is a legal question this project has not answered.
