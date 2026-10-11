# Real public SLA wording, simulated fleets (mock reader)

Contract text is real and public. Telemetry and monthly fee are simulated. No ground truth exists, so this shows behaviour, not accuracy.

## Amazon Compute (EC2) Service Level Agreement
7,646 characters. Expected: two uptime commitments (99.99% region, 99.5% instance) with their own credit tables.

| fleet uptime | verdict | credit / amount | why |
|---|---|---|---|
| 99.995% | COMPLIANT | - | All numeric SLA thresholds satisfied under every reading and data view (2 reading(s)) |
| 99.97% | ESCALATED | $0 to $1,200 at stake | outcome depends on how the contract is read (2 reading(s)) |
| 99.7% | ESCALATED | $0 to $1,200 at stake | outcome depends on how the contract is read (2 reading(s)) |
| 99.3% | BREACH | $1,200.00 | SLA breach: uptime shortfall 0.69 percentage points (holds under every reading and data view) (2 reading(s)) |
| 97% | BREACH | $3,600.00 | SLA breach: uptime shortfall 2.99 percentage points (holds under every reading and data view) (2 reading(s)) |
| 92% | BREACH | $12,000.00 | SLA breach: uptime shortfall 7.99 percentage points (holds under every reading and data view) (2 reading(s)) |

## Google Compute Engine Service Level Agreement
9,506 characters. Expected: several uptime commitments by configuration, each with credit tiers.

| fleet uptime | verdict | credit / amount | why |
|---|---|---|---|
| 99.995% | COMPLIANT | - | All numeric SLA thresholds satisfied under every reading and data view (3 reading(s)) |
| 99.97% | ESCALATED | $0 to $1,200 at stake | outcome depends on how the contract is read (3 reading(s)) |
| 99.7% | BREACH | $1,200.00 | SLA breach: uptime shortfall 0.29 percentage points (holds under every reading and data view) (3 reading(s)) |
| 99.3% | BREACH | $1,200.00 | SLA breach: uptime shortfall 0.69 percentage points (holds under every reading and data view) (3 reading(s)) |
| 97% | BREACH | $1,200.00 (needs review) | SLA breach: uptime shortfall 2.99 percentage points (holds under every reading and data view) (3 reading(s)) |
| 92% | BREACH | $3,000.00 (needs review) | SLA breach: uptime shortfall 7.99 percentage points (holds under every reading and data view) (3 reading(s)) |

## Microsoft Azure SLA for Virtual Machines
200,360 characters. Expected: uptime commitments with credit tables, in a long multi-service document.

| fleet uptime | verdict | credit / amount | why |
|---|---|---|---|
| 99.995% | ESCALATED | - | no numeric SLA threshold could be read from the contract text (1 reading(s)) |
| 99.97% | ESCALATED | - | no numeric SLA threshold could be read from the contract text (1 reading(s)) |
| 99.7% | ESCALATED | - | no numeric SLA threshold could be read from the contract text (1 reading(s)) |
| 99.3% | ESCALATED | - | no numeric SLA threshold could be read from the contract text (1 reading(s)) |
| 97% | ESCALATED | - | no numeric SLA threshold could be read from the contract text (1 reading(s)) |
| 92% | ESCALATED | - | no numeric SLA threshold could be read from the contract text (1 reading(s)) |
