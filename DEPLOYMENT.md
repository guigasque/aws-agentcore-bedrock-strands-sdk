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

### Knowledge Base layer (provisioned; enables Wave 2 RAG)

Design choice: **S3 Vectors** (`storageConfiguration.type=S3_VECTORS`) as the KB vector store
instead of OpenSearch Serverless. Rationale: dramatically simpler to provision as code (vector
bucket + index vs. collection + 3 policy types + data-plane index) and far cheaper (no always-on
OpenSearch OCUs — the exact cost risk the course Cleanup section warns about). Embedding model:
`amazon.titan-embed-text-v2:0` (1024-dim, cosine).

`infra/` modules (idempotent describe-or-create, each with a matching delete):
`common.py` (session/naming/tags/state/waiters), `iam.py` (KB service role, least privilege),
`s3_catalog.py` (source bucket + `product_catalog.txt`), `s3vectors.py` (vector bucket + index),
`knowledge_base.py` (KB + data source + ingestion with COMPLETE waiter), `outputs.py` (writes IDs
to `config.yaml` + runs `sync_config.py`), `provision_kb.py` (KB-only entry point).

Run: `python -m infra.provision_kb`. Resulting resources (account 024333017526, us-east-1):

| Resource | Name / ID |
|---|---|
| Source bucket | `cs-support-kb-src-024333017526-us-east-1` |
| Vector bucket | `cs-support-vectors-024333017526-us-east-1` |
| Vector index | `cs-support-index` (1024-dim, cosine) |
| KB service role | `arn:aws:iam::024333017526:role/cs-support-kb-service-role` |
| Knowledge Base | `CustomerSupportKB` → **KB_ID `YPXPQVRBJX`** |
| Data source | `cs-support-catalog` (`KMTALPXTF7`) |
| Ingestion job | `PW4TSROBWM` → COMPLETE |

Incident handled: newly-created IAM role needs ~10-60s to be assumable by Bedrock
(`CreateKnowledgeBase` → "unable to assume the given role"). `provision_kb.py` wraps KB creation in
a `wait_until` that retries on any "assume"/"accessdenied" message (idempotent; the role/bucket are
reused on retry).

Verification (code-only): `search_knowledge_base("return policy for electronics")` returned the
"Electronics: 15 days" chunk; full agent **T3** ("Platinum loyalty tier benefits") returned free
same-day shipping + 15% discount + priority support, grounded in the catalog. RAG works end to end.

_Remaining infra layers (Lambdas, API Gateway, Gateway, Memory) are added in Wave I._

---

## 5. Deploy, permissions, tests

### Wave 1 cloud validation (first real deploy)

- Agent name: `cs_support_agent`
- Agent ARN: `arn:aws:bedrock-agentcore:us-east-1:024333017526:runtime/cs_support_agent-yMZc9TGr2D`
- Execution role (auto-created): `AmazonBedrockAgentCoreSDKRuntime-us-east-1-50dd5f9114`
- Source S3 bucket (auto-created): `bedrock-agentcore-codebuild-sources-024333017526-us-east-1`
- Deployment type: `direct_code_deploy`, runtime `python3.13`, platform `linux/arm64`, memory disabled.

Config/deploy commands used:

```bash
agentcore configure --entrypoint main.py --name cs_support_agent \
  --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory \
  --region us-east-1 --non-interactive
agentcore deploy
agentcore status            # Endpoint DEFAULT (READY)
agentcore invoke '{"prompt": "Hello, what can you help me with?", "customer_id": "CUST-123", "session_id": "test-1"}'
```

Result: `agentcore invoke` returned a coherent Nova Lite response listing the agent's
capabilities. Wave 1 validated in the cloud.

### Incident & fix — runtime init crash on first deploy

First deploy succeeded but `invoke` failed with `RuntimeClientError: Runtime initialization time
exceeded`. CloudWatch logs showed the real cause: an **import crash**, not a timeout —

```
ImportError: browser was removed in v0.9.0 ... (strands_tools.browser)
```

Root cause: `pyproject.toml` pinned `strands-agents-tools>=0.2.21`, so the deployed runtime
resolved `0.9.0` (which removed `strands_tools.browser.AgentCoreBrowser`), while the local venv had
`0.8.12`. The top-level `from strands_tools.browser import AgentCoreBrowser` crashed module import.

Fix (two parts):
1. Pin `strands-agents-tools>=0.2.21,<0.9.0` in `pyproject.toml` so deploy == local.
2. Refactor `main.py` to **lazy imports + lazy client init**: the browser/MCP/code-interpreter
   imports moved inside the functions that use them (Waves 3–5), and `model`/`memory_client`/
   `_bedrock_runtime` became cached accessors (`get_model()`, `get_memory_client()`,
   `get_bedrock_runtime()`). Module import is now fast and cannot be broken by an optional
   dependency. Added `starter/.dockerignore` to keep `.venv` (436 MB) out of the bundle.

Follow-up for Wave 4: the starter's `AgentCoreBrowser` is gone in `strands-agents-tools >=0.9.0`.
We stay pinned `<0.9.0` for now; at Wave 4 decide whether to remain pinned or move to
`bedrock_agentcore.tools.browser_client.BrowserClient` + Playwright.

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
- **Wave 1** — runnable skeleton. Implemented `load_config()` (safe-fallback YAML loader),
  TODO 1 (`app = BedrockAgentCoreApp()`), TODO 2 (literals synced from config + `GATEWAY_URL` from
  config), TODO 3 (model/clients + externalized `SYSTEM_PROMPT` loader), and a minimal TODO 8
  entrypoint (validate prompt, uuid session fallback, identifier logging, tool-less Agent, defensive
  response extraction, specific-exception handling). Not-yet-implemented TODO 4/7 stubs converted to
  `NotImplementedError` to keep ruff green. Verified: `ruff check main.py` clean; `py_compile` OK;
  imports OK; local smoke invoke returned a coherent Nova Lite response; missing-prompt guard works.
  Deployable/runnable checkpoint met (tools/memory arrive in Waves 2–6).
  - Refinement: `_DEFAULT_CONFIG` replaced by `_MINIMAL_DEFAULTS` (only model_id/region/
    system_prompt_path) so `config.yaml` is the sole source of truth and the Python dict is just a
    start-up safety net; TODO 2 comments clarified (three literals are a `sync_config.py`-generated
    mirror of config.yaml because setup_permissions.py AST-reads them; `GATEWAY_URL` read from YAML).
  - Cloud fix: lazy imports + lazy client init (`get_model`/`get_memory_client`/`get_bedrock_runtime`);
    pinned `strands-agents-tools<0.9.0`; added `.dockerignore`. Deployed and invoked successfully
    (ARN `cs_support_agent-yMZc9TGr2D`). See §5 for the incident writeup.
- **Wave 2** — Knowledge Base RAG tool (TODO 6). Implemented `search_knowledge_base(query)`:
  `_is_configured(KB_ID)` guard → "Knowledge base not configured."; `get_bedrock_runtime().retrieve`
  with `numberOfResults` from `config.yaml:retrieval.top_k`; empty → no-results message; else join
  chunk texts with the configured separator; specific-exception handling → readable error; INFO log
  per call. Wired into the entrypoint tools list. Verified: ruff clean; compile OK; guard returns the
  not-configured message; live smoke — the model *called the tool* for a policy question and degraded
  gracefully (no hallucinated policy) since the KB is not provisioned yet. Full T3 passes once the KB
  exists (Wave I).
