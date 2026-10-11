# Real public SLA wording, simulated fleets (llm reader)

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
| 99.97% | ESCALATED | - | outcome depends on how the contract is read (3 reading(s)) |
| 99.7% | BREACH | $0.00 (needs review) | SLA breach: uptime shortfall 0.29 percentage points (holds under every reading and data view) (3 reading(s)) |
| 99.3% | BREACH | $0.00 (needs review) | SLA breach: uptime shortfall 0.69 percentage points (holds under every reading and data view) (3 reading(s)) |
| 97% | BREACH | $0.00 (needs review) | SLA breach: uptime shortfall 2.99 percentage points (holds under every reading and data view) (3 reading(s)) |
| 92% | BREACH | $0.00 (needs review) | SLA breach: uptime shortfall 7.99 percentage points (holds under every reading and data view) (3 reading(s)) |

## Microsoft Azure SLA for Virtual Machines
200,360 characters. Expected: uptime commitments with credit tables, in a long multi-service document.

| fleet uptime | verdict | credit / amount | why |
|---|---|---|---|
| 99.995% | ESCALATED | - | extractor failed: AI reader failed after 5 attempts. Last error: Attempt 1/5: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50642, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
Traceback (most recent call last):
  File "D:\Downloads new\final-project-full\Michelin_SLA_Harness_Updated\michelin_sla_harness_updated\agents\clause_reader.py", line 282, in read_clauses_llm
    completion = client.chat.completions.create(
        model=_MODEL_NAME,
    ...<5 lines>...
        response_format={"type": "json_object"},
    )
openai.APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50642, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
 (0 reading(s)) |
| 99.97% | ESCALATED | - | extractor failed: AI reader failed after 5 attempts. Last error: Attempt 1/5: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
Traceback (most recent call last):
  File "D:\Downloads new\final-project-full\Michelin_SLA_Harness_Updated\michelin_sla_harness_updated\agents\clause_reader.py", line 282, in read_clauses_llm
    completion = client.chat.completions.create(
        model=_MODEL_NAME,
    ...<5 lines>...
        response_format={"type": "json_object"},
    )
openai.APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
 (0 reading(s)) |
| 99.7% | ESCALATED | - | extractor failed: AI reader failed after 5 attempts. Last error: Attempt 1/5: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
Traceback (most recent call last):
  File "D:\Downloads new\final-project-full\Michelin_SLA_Harness_Updated\michelin_sla_harness_updated\agents\clause_reader.py", line 282, in read_clauses_llm
    completion = client.chat.completions.create(
        model=_MODEL_NAME,
    ...<5 lines>...
        response_format={"type": "json_object"},
    )
openai.APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
 (0 reading(s)) |
| 99.3% | ESCALATED | - | extractor failed: AI reader failed after 5 attempts. Last error: Attempt 1/5: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
Traceback (most recent call last):
  File "D:\Downloads new\final-project-full\Michelin_SLA_Harness_Updated\michelin_sla_harness_updated\agents\clause_reader.py", line 282, in read_clauses_llm
    completion = client.chat.completions.create(
        model=_MODEL_NAME,
    ...<5 lines>...
        response_format={"type": "json_object"},
    )
openai.APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
 (0 reading(s)) |
| 97% | ESCALATED | - | extractor failed: AI reader failed after 5 attempts. Last error: Attempt 1/5: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
Traceback (most recent call last):
  File "D:\Downloads new\final-project-full\Michelin_SLA_Harness_Updated\michelin_sla_harness_updated\agents\clause_reader.py", line 282, in read_clauses_llm
    completion = client.chat.completions.create(
        model=_MODEL_NAME,
    ...<5 lines>...
        response_format={"type": "json_object"},
    )
openai.APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50814, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
 (0 reading(s)) |
| 92% | ESCALATED | - | extractor failed: AI reader failed after 5 attempts. Last error: Attempt 1/5: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50452, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
Traceback (most recent call last):
  File "D:\Downloads new\final-project-full\Michelin_SLA_Harness_Updated\michelin_sla_harness_updated\agents\clause_reader.py", line 282, in read_clauses_llm
    completion = client.chat.completions.create(
        model=_MODEL_NAME,
    ...<5 lines>...
        response_format={"type": "json_object"},
    )
openai.APIStatusError: Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` in organization `org_01m4ekxzt0e95r7dpa704tk4c0` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 50452, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
 (0 reading(s)) |
