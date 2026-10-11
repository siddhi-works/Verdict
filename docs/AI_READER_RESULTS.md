# AI reader results

Model: `openai/gpt-oss-120b` via Groq (free tier: 8,000 tokens/minute, 1,000 requests/day). Prompt version `clause-reader-v2`.
The AI never decides a verdict; it only lists candidate numbers with quotes. In AI mode its output is **merged with the pattern
reader** (union), then verified, enumerated and decided by the same deterministic engine.

## 1. Full generated set, first attempt: not a valid measurement

194 contracts, no request pacing. Result: 0 wrong automatic verdicts, accuracy 64.4%.
**56 of the 69 over-escalations were HTTP 429 rate-limit failures**, not AI misreadings; the system failed safe and escalated.
Only 13 were genuine differences between what the AI and the pattern reader read. This accuracy figure is not reported as the AI's
accuracy. (Raw output kept as `data/v2/ai_results_run1_rate_limited.json`.)

## 2. Stratified sample, 10 contracts (paced, cached)

One contract from each of 10 categories where the AI should matter. **This is a sample, not an accuracy estimate (n = 10).**

| | Legacy single-reading gate | Envelope engine, AI reader |
|---|---|---|
| Correct | 5 / 10 | 9 / 10 |
| Wrong automatic verdicts | 1 | **0** |
| Over-escalations | 4 | 1 |

- **Gained over the pattern reader:** `precedence_separate_clause` (a precedence clause in a different sentence from the
  number). The pattern reader escalates every such contract (8/8 in the full set); the AI resolved the sampled one correctly (BREACH).
- **Not solved:** `response_vs_resolution` (V2-019) was escalated although the correct answer was COMPLIANT. Safe, but a miss.
- The pattern reader alone gets 94.3% on the full 194 (0 wrong automatic verdicts). The sample cannot show the AI is better
  overall, only that it can resolve at least one case class the pattern reader cannot.

## 3. Real public SLA wording (AWS, Google, Azure), simulated telemetry and fee

| Document | Pattern reader | AI + pattern reader |
|---|---|---|
| AWS EC2 | Both commitments and both credit tables read; escalates exactly where the two commitments disagree; 10/30/100% of fee | Same result after fixes (see 4). |
| Google Compute Engine | Three targets, partial credit tables; $1,200 / $3,000 range flagged for review | Three targets read, but the AI's and the pattern reader's credit tables differ, so no tier is used: breaches are automated but the amount is **flagged "needs review", $0**. More conservative, no better. |
| Azure (200,000 characters) | No threshold found, escalated | Document exceeds the 8,000-token limit; reader fails safe, escalated. Needs chunking. |

So on real documents the AI reader did **not** improve on the pattern reader. It matched it on AWS, was more conservative on Google, and could not process Azure.

## 4. Defects the live model exposed (all fixed, with tests)

1. Crash when the AI returned a credit tier with a missing bound (`None`) and the readers' tables were compared.
2. The AI listed credit-table boundaries (99.0%, 95.0%) as uptime targets. Now removed deterministically (a number whose quote is a tier row is a boundary).
3. The AI labelled sentences that state a number, and unrelated boilerplate (conflict, exclusion and claims clauses), as "vague".
   Now only sentences that actually hedge count as vague, and a sentence that states a verified threshold never does.
4. A percent-of-fee SLA whose credit table could not be read fell back to a per-point dollar rate. It now returns "amount needs review" with no number.
5. My own first pacing/backoff edit silently did not apply, which is why the first full run hit rate limits. Fixed and verified.

## 5. Limits

- 10 contracts is anecdotal. A full AI run needs about 55 minutes on this tier (16 s/contract); `run_ai_report.ps1` does it and resumes from `data/v2/ai_cache`.
- One model, one prompt version, one run per contract (no repeat runs, so no variance estimate).
- The prompt was changed after seeing failures on the real documents, so results on those documents are not a held-out test.
- Telemetry and fee for the real documents are simulated; there are no ground-truth labels for them.
