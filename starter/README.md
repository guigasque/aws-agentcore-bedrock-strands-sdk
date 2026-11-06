# Customer Support AI Agent — Developer Guide

Implementation of a customer support agent on **Amazon Bedrock AgentCore** with the **Strands SDK**.
This guide covers local setup, the configuration model, linting, the wave-based build, and the
all-code deploy/destroy workflow. **New here? Start with [`../docs/HOW_IT_WORKS.md`](../docs/HOW_IT_WORKS.md)**
for the end-to-end narrative. The course brief is the root `README.md`; the operational build
log is `../DEPLOYMENT.md`; architecture diagrams are `../docs/ARCHITECTURE.md`; the full spec
(requirements, design, tasks) is `../.kiro/specs/customer-support-agent/`.

## Capabilities

| Capability | How | Test |
|---|---|---|
| Order tracking & refunds | AgentCore Gateway (MCP) → API Gateway/Lambda | T1, T2 |
| Product & policy answers (RAG) | `search_knowledge_base` → Bedrock KB Retrieve | T3 |
| Cross-session memory | `MemoryHook` → AgentCore Memory (facts + preferences) | T4 |
| Exact loyalty discounts | `calculate_loyalty_discount` → Code Interpreter sandbox | T5 |
| Live web browsing | AgentCore Browser tool | T6 |

## Prerequisites

- Python 3.13+, Node.js 18+ (optional, for MCP Inspector). Docker is optional — only needed for
  `agentcore deploy --local`; the cloud `agentcore deploy` (direct_code_deploy) does not use it.
- AWS credentials in `../.env` (temporary lab creds are fine). Amazon Nova 2 Lite enabled.

## 1. Environment (venv + pip)

```bash
cd starter
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .            # dependencies from pyproject.toml
pip install ruff         # dev lint/format
```

Load credentials in each shell:

```bash
set -a; source ../.env; set +a
aws sts get-caller-identity   # sanity check
```

## 2. Configuration model (single source of truth)

`config.yaml` holds every tunable value (model id, region, gateway URL, KB/memory IDs, retrieval
knobs, loyalty rates, timeouts, system-prompt path). `main.py` reads it via `load_config()` with
safe defaults.

Three values — `REGION`, `KB_ID`, `MEMORY_ID` — must also exist as **literal assignments** in
`main.py` because the provided `setup_permissions.py` AST-reads them. Never hand-edit those
literals; edit `config.yaml` and run:

```bash
python sync_config.py          # mirror the three literals from config.yaml (idempotent)
python sync_config.py --check  # CI-friendly: non-zero if out of sync
```

After provisioning, `infra/outputs.py` writes the real IDs into `config.yaml` and runs
`sync_config.py` automatically — so you never edit config by hand.

The system prompt lives in `system_prompt.txt` (referenced by `config.yaml`), not inline in code.

## 3. Linting / formatting

```bash
ruff check .     # lint
ruff format .    # format
```

Ruff clean + `python -m py_compile main.py` gate every change.

## 4. Infrastructure as code (no console)

All AWS resources are provisioned and destroyed by code. The agent Runtime uses the AgentCore
toolkit (also CLI, not UI).

```bash
python -m infra.provision   # IAM, Lambdas, REST API, S3+KB (S3 Vectors), Gateway, Memory (idempotent)
python -m infra.verify      # identity, MCP tool listing, KB retrieve probe, Memory ACTIVE
python -m infra.destroy     # reverse teardown (idempotent)
```

To provision only the Knowledge Base layer (useful while iterating on RAG):

```bash
python -m infra.provision_kb
```

### How the knowledge base works

The KB answers **only** from `product_catalog.txt`. Provisioning uploads that file to an S3 source
bucket, then an ingestion job **chunks** it, **embeds** each chunk with Amazon Titan Text Embeddings
v2 (`amazon.titan-embed-text-v2:0`), and stores the vectors in an **S3 Vectors** index (chosen over
OpenSearch Serverless: simpler to script and no always-on cost). At query time, `search_knowledge_base`
calls the Bedrock Retrieve API, which semantically matches the question and returns the relevant
chunks for the model to answer from.

The KB is a **snapshot** taken at ingestion time — it does not read the file live. After editing
`product_catalog.txt`, re-run `python -m infra.provision_kb` to re-upload and re-ingest. The provisioner
writes the resulting `KB_ID` into `config.yaml` and syncs it into `main.py` automatically.

## 5. Deploy & run

**One-shot (recommended)** — wrapper scripts at the repo root run the whole lifecycle and support
`--dry-run` (prints the plan + read-only checks, changes nothing):

```bash
./deploy.sh --dry-run     # preview: plan + sts identity + IAM policy preview + infra.verify
./deploy.sh               # provision infra -> configure -> deploy -> permissions -> verify
```

**Or step by step:**

```bash
agentcore configure --entrypoint main.py --name <your-agent-name> \
    --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory
agentcore deploy                     # cloud deploy; no Docker (use --local for a container)
python setup_permissions.py          # least-privilege policy on the runtime role
python -m infra.agent_permissions    # Code Interpreter grant (setup_permissions omits it)
agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
```

**Teardown** — `./teardown.sh` (or `./teardown.sh --dry-run` to list what would be deleted):

```bash
./teardown.sh             # agentcore destroy + python -m infra.destroy (reverse order)
```

## 6. Functional tests

```bash
agentcore invoke '{"prompt": "Can you track order ORD-001?", "customer_id": "CUST-123", "session_id": "t1"}'
agentcore invoke '{"prompt": "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.", "customer_id": "CUST-123", "session_id": "t2"}'
agentcore invoke '{"prompt": "What are the benefits of the Platinum loyalty tier?", "customer_id": "CUST-123", "session_id": "t3"}'
agentcore invoke '{"prompt": "Hi, I am Jane. I prefer concise responses.", "customer_id": "CUST-123", "session_id": "s-A"}'
# wait ~30s for memory extraction, then:
agentcore invoke '{"prompt": "Do you remember my name and communication preference?", "customer_id": "CUST-123", "session_id": "s-B"}'
agentcore invoke '{"prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.", "customer_id": "CUST-123", "session_id": "t5"}'
agentcore invoke '{"prompt": "Go to https://www.udacity.com and tell me the page title.", "customer_id": "CUST-123", "session_id": "t6"}'
```

## 7. Project layout

```
.                        # repo root
├── deploy.sh            # one-shot: provision → deploy → permissions → verify (--dry-run)
├── teardown.sh          # one-shot: agentcore destroy → infra.destroy (--dry-run)
├── package_submission.sh
└── starter/
    ├── main.py              # the agent (TODOs filled across waves)
    ├── config.yaml          # single source of truth
    ├── system_prompt.txt    # externalized system prompt
    ├── sync_config.py       # mirror 3 literals into main.py (idempotent)
    ├── setup_permissions.py # provided; grants runtime-role access (do not modify)
    ├── ruff.toml            # lint/format config
    ├── pyproject.toml       # dependencies
    ├── product_catalog.txt  # KB knowledge source — uploaded, chunked & embedded into the KB
    ├── lambda/              # order_tracker.py, refund_processor.py, lambda_schema (deployed as-is)
    └── infra/               # idempotent boto3 provisioner/destroyer (all-code IaC)
```

## Build approach

Built in **waves**; each wave is independently deployable/runnable and committed separately.
See `../.kiro/specs/customer-support-agent/tasks.md` for the wave plan and
`../DEPLOYMENT.md` for the running log.
