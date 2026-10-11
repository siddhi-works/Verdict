# Verdict: Agentic SLA Auditor

Built at the Michelin x AI Tinkerers Harness Engineering Hackathon (Pune, Operations & Compliance track) and extended afterwards. An independent project: not an official Michelin product.

Audits B2B fleet contracts against vehicle telemetry. The rule it is built on:

> **Automate a decision only if it stays the same under every reasonable reading of the contract and every reasonable reading of the data. If it flips, a human decides, with a one-page brief and the dollar range.**

A contract that contradicts itself (12h in one clause, 24h in another) is only escalated when the contradiction changes the answer. If the slowest repair was 31 hours, it is a breach under both readings and is automated.

## How a contract is audited

| Step | Who | What |
|---|---|---|
| 1 Read | AI reader + pattern reader | Each lists every candidate number with the exact sentence it came from. Neither gives a verdict. |
| 2 Verify | plain code | Each quote must be in the contract and contain the number. Invented thresholds are dropped. |
| 3 Fan out | plain code | Every plausible reading is enumerated (conflicts, vague wording, precedence clauses). |
| 4 Run | plain code | Each reading is run against a *trusted* and an *inclusive* view of the telemetry (`agents/data_trust.py`). |
| 5 Decide | plain code | All cells agree -> `COMPLIANT` / `BREACH`. Anything else -> `ESCALATED` with a brief. |
| 6 Seal | plain code | The evidence is stored as a proof bundle in a hash-chained ledger. Replay it offline with no AI. |

Hard rules: the AI never produces a verdict; missing or unusable data never produces a number; an escalation never carries an automated amount; a penalty not backed by a contract clause is flagged for commercial review.

## Run it

```powershell
python -m pytest -q                                   # 108 tests
python harness.py --mock                              # offline run on the bundled 50 contracts
python -m data_gen.generate --n 200 --seed 7 --out data/v2    # realistic labelled set (~190 contracts, ~80k telemetry rows)
python harness.py --mock --data data/v2
python -m eval.run_eval --data data/v2                # score against ground_truth.json
python harness.py --verify-ledger --data data/v2      # check the hash chain
python harness.py --replay <proof_id> --data data/v2  # re-check one verdict, no AI, no network
streamlit run ui/app.py                               # dashboard: envelope, review queue, proofs
```

AI reader: put `OPENAI_API_KEY` (and optionally `SLA_API_BASE_URL`, `SLA_EXTRACTOR_MODEL`) in `.env`, then run without `--mock`. The pattern reader always runs alongside it, and a disagreement between the two only adds readings, so it can make the system more careful and never less.

### API

```powershell
$env:SLA_API_TOKENS = '{"<token>": {"user": "alice", "role": "reviewer", "tenant": "michelin"}}'
uvicorn service.main:app_factory --factory
```

`POST /audit`, `GET /audit/{proof_id}/proof`, `GET /audit/{proof_id}/verify`, `GET /escalations`, `POST /escalations/{proof_id}/resolve`, `GET /metrics`, `GET /ledger/verify`. Roles: analyst (read), reviewer (audit and resolve), admin. Each tenant has its own ledger. `SLA_API_INSECURE_DEMO=1` creates random one-off tokens for local demos.

## Test data

`data_gen/generate.py` writes the *facts* first (thresholds, which clause governs, what the fleet did), then renders them into 400-1000 word contracts and noisy telemetry (timestamps, planned maintenance, duplicates, FAULT sensors, impossible values, 6-600 trucks per fleet). The answers go in `ground_truth.json`, never in the contract file. 20 scenario families, including contradictions that do and do not change the outcome, precedence clauses, vague wording, missing or mostly-corrupt telemetry, planned-maintenance exclusions, numbers in words or days, and distractor clauses.

## Layout

```
agents/   clause_reader (AI + pattern readers), quote_verifier, data_trust, number_words, parser, extractor (legacy single-reading reader)
core/     rules_ir (readings), envelope (decision), pipeline, ledger, verify_proof, precedents, brief, outbox, gatekeeper, schemas
data_real/  public SLA links, fetcher and behaviour study
api/      Starlette service       data_gen/  labelled data generator       eval/  scoring against ground truth
ui/       Streamlit dashboard     tests/     unit, property and API tests
```

## Real public SLAs (behaviour study)

`data_real/` runs the wording of real, public cloud SLAs (AWS EC2, Google Compute Engine, Microsoft Azure) through the pipeline. The repo stores **links only** (`manifest.json`); `python -m data_real.fetch_docs` caches the text locally (git-ignored) because the pages belong to their publishers. Then `python -m data_real.run_real --out data_real/RESULTS.md` pairs each document with simulated fleets at a sweep of uptime levels.

Real SLAs forced three additions: **tiered credits** (a percentage of the monthly fee by uptime band, in several table notations), **uptime-only profiles** (an operator declares the SLA has no repair-time clause; this is never assumed), and a guard so a credit table with a missing row can never silently produce a $0 credit.

What it shows (simulated fleets, simulated $12,000 monthly fee):

- **AWS EC2** publishes two commitments (99.99% regional, 99.5% per instance). At 99.7% fleet uptime the engine escalates with "$0 to $1,200 at stake"; at 97% both commitments are breached under the same 30% tier, so it automates a $3,600 credit.
- **Google Compute Engine** has different credit tables for multi-zone and single instances. At 97% they give 25% or 10%, so the engine reports the range ($1,200 to $3,000) and flags it for review.
- **Azure** (a 200,000-character multi-service document) yields no readable threshold, so it is escalated, not guessed.

What this does **not** show: there are no ground-truth labels for these documents, so no accuracy figure is claimed. Telemetry and fee are simulated, and the pattern reader only handles the table styles above (the AI reader is the intended route for the rest). Cloud "instances" are modelled as fleet vehicles averaged together, which is a simplification of per-instance SLAs.

## AI reader results

Measured results for the AI reader, with the caveats (a 10-contract sample, rate-limit confound in the first full run, and no improvement over the pattern reader on real public documents) are in [`docs/AI_READER_RESULTS.md`](docs/AI_READER_RESULTS.md).

## Ablation and baseline

What each part of the harness is worth, and a same-model single-prompt baseline, are in [`docs/ABLATION_AND_BASELINE.md`](docs/ABLATION_AND_BASELINE.md). Reproduce with `python -m eval.ablation --data data/v2` and `python -m eval.run_baseline`.

## Deploy the web app to Vercel

Vercel runs short serverless functions, so it cannot host the Streamlit console. The Vercel deployment is a static page
(`public/index.html`) plus a Python API function (`api/index.py`) that serves the pre-audited bundle in `demo/`.
The Streamlit console stays for local and container use.

```powershell
python -m scripts.build_demo_bundle     # rebuild demo/ (only needed when the engine or data changes)
python -m scripts.dev_web 3000          # try it locally at http://localhost:3000
```

Then import the GitHub repo in Vercel (framework preset: Other, no build command). In the project settings add:

| Variable | Purpose |
|---|---|
| `SLA_SESSION_SECRET` | Signs session tokens. Set a long random value. |
| `SLA_UI_AUTOFILL` | `0` hides the demo password prefill on the sign-in page (recommended for anything public). |
| `SLA_UI_PASSWORD` | Only if you reseeded `ui/users.json` with a custom password. |

Limits of the deployment: it serves the bundled results and cannot run new audits; rulings are written to the function's temp
directory, so they are not permanent. `requirements.txt` is deliberately small (the Vercel install); local work uses `requirements-full.txt`.

## Console accounts

The console (`streamlit run ui/app.py`) signs in with fictional staff accounts. Passwords are stored only as salted PBKDF2 hashes
in `ui/users.json`; create or reset them with `python -m ui.seed_users` (set `SLA_UI_PASSWORD` to choose the password, otherwise the
local-demo default printed by the script is used).

| Account | Role | Can |
|---|---|---|
| priya.deshmukh@michelin-sla.example | Analyst | View audits, evidence, telemetry |
| arjun.kulkarni@michelin-sla.example | Reviewer | Everything above, and record rulings on escalations |
| camille.laurent@michelin-sla.example | Administrator | Everything above, and run audits |

For local demos the sign-in page offers a "Sign in as" picker that fills in the email and (masked) password, so one click signs in. Set `SLA_UI_AUTOFILL=0` to turn this off for anything shared.

The signed-in person's name is recorded as the actor on every ruling in the audit ledger. There is no lockout or single sign-on; this is
a local demo login, not production authentication.

## Architecture

What is built, and a labelled target-state deployment (not implemented), are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Known limits

- Fleet-wide thresholds only, evaluated over one month of data. Per-vehicle-class tiers, multi-month windows and per-instance SLAs are not modelled yet.
- The pattern reader cannot follow a precedence clause that lives in a separate sentence from the number; such contracts escalate (safe, but needs a person). The AI reader is meant to resolve these; it has been tested with a stub client only, not yet against a live model.
- Results on generated data show the engine's logic, not its accuracy on real Michelin contracts. A pilot on real contracts is the next step.
