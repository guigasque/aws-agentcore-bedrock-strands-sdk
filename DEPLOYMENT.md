# Deployment & Build Log — Customer Support AI Agent

Operational companion to the spec in `.kiro/specs/customer-support-agent/`. This is a living log
updated at every wave hand-off with what was actually built and verified. Architecture diagrams
live in `docs/ARCHITECTURE.md`; the developer guide is `starter/README.md`.

- **AWS account**: `687113236987` (Vocareum lab, assumed-role `voclabs`)
- **Region**: `us-east-1`
- **Model**: Amazon Nova 2 Lite — `global.amazon.nova-2-lite-v1:0` (verified enabled)
- **Credentials**: loaded from `./.env` (temporary session credentials — expire; re-source per shell)
- **Python env**: `starter/.venv` (venv, Python 3.13.4) — NOT `uv`

> Credentials in `.env` are temporary lab credentials. Re-source in each shell:
> `set -a; source .env; set +a`. No secrets are committed (`.env` is gitignored).

---

## 0. Environment facts (probed)

| Item | Result |
|------|--------|
| Docker | UP |
| Python | 3.13.4 |
| `uv` | not installed — using `venv` + `pip` |
| System AWS CLI | 2.27.33 — lacks `bedrock-agentcore-control` (too old for AgentCore control plane) |
| **venv boto3** | **1.43.123 — HAS the full AgentCore control plane** (see §2) |
| Terraform | v1.11.4 (installed; not used — see decision below) |
| Node.js | v20.18.0 |
| Nova 2 Lite access | enabled (`amazon.nova-2-lite-v1:0`) |

**IaC decision:** provision **100% as code, no console**. Everything around the Runtime is created
by an idempotent boto3 package (`starter/infra/`); the Runtime itself uses the sanctioned toolkit
(`agentcore configure/deploy/destroy`) — also code/CLI, not UI. Terraform was evaluated but the
`hashicorp/aws` provider's AgentCore coverage is still maturing; boto3 has every create+delete op
today, so Option A (boto3 orchestrator) was chosen.

### Installed dependency versions (pinned at build time)

- `strands-agents 1.63.0`, `strands-agents-tools 0.8.12`
- `bedrock-agentcore 1.25.3`, `bedrock-agentcore-starter-toolkit 0.3.14`
- `mcp 2.3.0`, `boto3 1.43.123`, `playwright 1.64.1`
- dev: `ruff 0.17.4`

---

## 1. Python environment setup (venv)

```bash
cd starter
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .            # from pyproject.toml
pip install ruff         # dev lint/format tool
```

Per-shell activation:

```bash
cd starter && source .venv/bin/activate
set -a; source ../.env; set +a   # load AWS creds
```

---

## 2. SDK & control-plane verification (why the code is shaped as it is)

Probed against the installed packages before writing code:

- MCP: `mcp.client.streamable_http.streamable_http_client`, `strands.tools.mcp.mcp_client.MCPClient` ✅
- Browser: `AgentCoreBrowser(region=...)` exposes a `.browser` Strands tool ✅
- Memory: `MemoryClient.get_memory_strategies / retrieve_memories(namespace, query, top_k) /
  create_event(messages=[(text, role)])` ✅
- Hooks: `HookRegistry.add_callback(EventType, cb)` ✅
- Code Interpreter: `code_session(region).invoke("executeCode", {...})` ✅ — live response shape:
  `resp = {"stream": [...]}`, each `event = {"result": {"content":[{"type":"text","text": "<stdout>"}],
  "structuredContent": {"stdout": ..., "exitCode": 0}, "isError": false}}`.

**AgentCore control plane in venv boto3 1.43.123 (create + delete all present):**
`bedrock-agentcore-control`: CreateGateway/GatewayTarget/Memory/AgentRuntime + deletes;
`bedrock-agent`: CreateKnowledgeBase/DataSource/StartIngestionJob + deletes;
`opensearchserverless`: CreateCollection + security/network/data-access policies + deletes.

---

## 3. Configuration architecture

`starter/config.yaml` is the single source of truth. `main.py` keeps exactly three **literals**
(`REGION`, `KB_ID`, `MEMORY_ID`) because `setup_permissions.py` AST-reads them; `sync_config.py`
mirrors those from `config.yaml` (idempotent). `infra/outputs.py` writes the provisioned
`gateway_url`/`knowledge_base_id`/`memory_id` into `config.yaml` then runs `sync_config.py`, so no
config is hand-edited.

```
config.yaml ──load_config()──▶ main.py (model, rates, prompts, GATEWAY_URL, ...)
     │
     └──sync_config.py──▶ main.py literals: REGION, KB_ID, MEMORY_ID ──▶ setup_permissions.py (AST read)
```

Verified in Wave 0: `sync_config.py` is ruff-clean, set `REGION="us-east-1"`, preserved trailing
comments, and is idempotent (second run is a no-op).

---

## 4. Infrastructure provisioning (all code — `starter/infra/`)

_To be filled in Wave I. Resources (deterministic names, idempotent): IAM roles, `order-tracker`
and `refund-processor` Lambdas, REST API + stage `prod`, S3 bucket + `product_catalog.txt`,
OpenSearch Serverless collection + policies + index, managed Knowledge Base + data source +
ingestion, AgentCore Gateway + 2 targets, AgentCore Memory + 2 strategies._

Entry points: `python -m infra.provision` / `python -m infra.destroy` / `python -m infra.verify`.
Resulting resource IDs recorded here (no secrets).

---

## 5. Deploy, permissions, tests

_To be filled in Wave 8._

```bash
# full lifecycle (all code)
python -m infra.provision
agentcore configure --entrypoint main.py --name <name> \
    --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory
agentcore deploy
python setup_permissions.py
python -m infra.verify
# ... six tests ...
agentcore destroy
python -m infra.destroy
```

---

## 6. Build log (per wave)

- **Wave 0** — spec committed; `.gitignore`; `config.yaml`, `system_prompt.txt`, `ruff.toml`,
  `sync_config.py` (ruff-clean, idempotent, verified); docs (`DEPLOYMENT.md`, `starter/README.md`,
  root README banner, `docs/ARCHITECTURE.md`). No `main.py` logic yet.
