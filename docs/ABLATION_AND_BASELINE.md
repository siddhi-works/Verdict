# Ablation and single-prompt baseline

Both experiments use the generated labelled set (`data/v2`, 194 contracts, answers kept in `ground_truth.json`).
Reproduce: `python -m eval.ablation --data data/v2` and `python -m eval.run_baseline --data data/v2 --categories ...`.

## 1. Ablation: what each part is worth (pattern reader, 194 contracts)

Each row switches off one part of the harness. "Wrong automatic" = a COMPLIANT or BREACH verdict that differs from the
correct answer (the dangerous error). "Over-escalation" = a clear-cut contract sent to a human.

| Variant | Accuracy | Wrong automatic | Over-escalations | Escalation rate |
|---|---|---|---|---|
| **Full system** | **94.3%** | **0** | 11 | 37.1% |
| minus envelope (take the first reading only) | 90.2% | **19** | 0 | 23.7% |
| minus data views (trust every parseable row) | 90.2% | **8** | 11 | 33.0% |
| minus scope filter (check all events, not only critical) | 88.1% | **11** | 12 | 37.6% |
| minus precedence handling | 90.2% | 0 | **19** | 41.2% |
| minus vague-wording handling | 94.3% | 0 | 11 | 37.1% |

Reading it:
- **Envelope, data views and scope filter each prevent wrong automatic verdicts** (19, 8 and 11 respectively on this set). Without the envelope the system looks more "efficient" (23.7% escalated) only because it guesses.
- **Precedence handling does not affect safety**, but removing it sends 8 more contracts to a human, so it buys automation.
- **Vague-wording handling showed no measurable effect on this set.** It is kept as a safeguard; this data does not demonstrate that it matters, and the report should not claim it does.

### Fault injection: the quote verifier

A reader that silently misreads every number (every repair time 1.5x too loose, every uptime target 1 point too low), simulating a hallucinating AI. This is a stress test, not a measured model error rate.

| Variant | Accuracy | Wrong automatic | Over-escalations | Escalation rate |
|---|---|---|---|---|
| with quote verifier | 31.4% | **0** | 133 | 100% |
| without quote verifier | 76.8% | **31** | 14 | 33.5% |

With the verifier, every misread number fails verification, so the system refuses to automate anything (safe, but useless in this extreme case).
Without it, the misread numbers are used and 31 wrong verdicts are issued automatically. Note that accuracy is higher without the
verifier: many misreadings still land on the right answer. The verifier trades availability for safety, which is the intended design.

## 2. Single-prompt baseline vs the harness (same model, same 10 contracts)

Same model (`openai/gpt-oss-120b`), temperature 0. The baseline gets the full contract text and a telemetry summary computed by code
(fleet uptime, slowest repair overall and for critical events, counts of faulty rows), so it never does arithmetic over raw rows.
It must read the contract and decide the verdict itself. The 10 contracts are one per category where the AI should matter most.

| Contract | Category | Correct answer | Single prompt | Harness |
|---|---|---|---|---|
| V2-001 | vague repair clause, uptime met | ESCALATED | COMPLIANT (wrong) | ESCALATED |
| V2-002 | contradiction, outcome unaffected | BREACH | BREACH | BREACH |
| V2-004 | numbers in words and days | COMPLIANT | COMPLIANT | COMPLIANT |
| V2-006 | precedence, same sentence | BREACH | BREACH | BREACH |
| V2-007 | clean | COMPLIANT | COMPLIANT | COMPLIANT |
| V2-009 | precedence, separate clause | BREACH | BREACH | BREACH |
| V2-014 | contradiction that decides the outcome | ESCALATED | COMPLIANT (wrong) | ESCALATED |
| V2-017 | doubtful telemetry decides the outcome | ESCALATED | BREACH (wrong) | ESCALATED |
| V2-019 | response time vs repair time | COMPLIANT | ESCALATED | ESCALATED |
| V2-110 | uptime breach | BREACH | BREACH | BREACH |

| | Single prompt | Harness |
|---|---|---|
| Correct | 6 / 10 | 9 / 10 |
| Wrong automatic verdicts | **3** | **0** |

The single prompt was right on every case that has a clear answer, and **wrong in exactly the three cases that needed it to say "I am not sure"**:
it confidently automated a vague clause, a decisive contradiction, and a faulty-sensor-dependent breach. That is the failure the harness is built to prevent.
Both missed V2-019 (safe escalation).

## 3. Limits

- n = 10 for the baseline: one sample per category, one run each, temperature 0. It shows the pattern, not a rate. A full run needs about 55 minutes on this tier.
- The baseline was given computed telemetry statistics. A baseline fed raw rows would likely do worse; this comparison is deliberately the favourable one.
- Contracts and ground truth come from the same generator as the engine's development data, so results show behaviour on that distribution only.
- The ablation uses the pattern reader; the AI reader was not ablated.
