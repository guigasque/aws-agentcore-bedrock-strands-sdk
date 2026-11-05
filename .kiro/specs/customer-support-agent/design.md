# Design — Customer Support AI Agent (Amazon Bedrock AgentCore + Strands)

## Overview

> **SDK surface verified** against the installed versions (`strands-agents 1.63.0`,
> `bedrock-agentcore 1.25.3`, `strands-agents-tools 0.8.12`, `mcp 2.3.0`). All import paths and
> call signatures in the starter resolve; the Code Interpreter response shape was confirmed with a
> live call. The earlier "SDK drift" risk is therefore resolved; specifics are noted inline below.

The agent is a single Strands `Agent` served by a `BedrockAgentCoreApp` and invoked through one
`@app.entrypoint` coroutine. Each request builds a per-invocation agent wired with:

- **Reasoning model**: Amazon Nova 2 Lite via `BedrockModel("global.amazon.nova-2-lite-v1:0")`.
- **Local tools**: `search_knowledge_base` (RAG), `calculate_loyalty_discount` (Code Interpreter),
  and the AgentCore Browser tool.
- **Remote tools**: order/refund tools loaded at request time from the AgentCore Gateway over MCP.
- **Memory**: a `MemoryHook` that reads customer context before the model answers and writes the
  turn after it answers, using AgentCore Memory with two strategies (facts, preferences).

This design maps 1:1 onto the eight TODOs in `starter/main.py` and does not alter the provided
Lambda, permissions script, or catalog.

### Why per-invocation agent construction

AgentCore Runtime invocations are independent and the MCP Gateway connection is a context manager
(`with MCPClient(...) as client:`). Gateway tools are only valid inside that context, so the
`Agent` must be created and run while the context is open. The `MemoryHook` and browser are also
bound to the current `actor_id`/`session_id`, which are per-request. Building everything inside
`invoke()` keeps tool handles live and memory scoped correctly, at the cost of a small per-request
setup. This matches the starter's structure and the Strands MCP usage pattern.

## Engineering standards (cross-cutting, apply to every wave)

This is built to a production-grade bar, not just to pass the rubric. Every wave inherits:

### Configuration — externalized, single source of truth (no magic values)

Hard constraint: `setup_permissions.py` (which must not be modified) AST-reads `KB_ID`,
`MEMORY_ID`, and `REGION` as **literal string assignments** in `main.py` (`ast.literal_eval` +
regex). Those three therefore **must stay as literals** in `main.py`. Everything else is
externalized.

Design: a `config.yaml` next to `main.py` is the source of truth; a small `load_config()` reads it
at import time with **safe fallbacks** so the agent still starts if the file is absent from the
deployed bundle.

```yaml
# config.yaml
model_id: "global.amazon.nova-2-lite-v1:0"
region: "us-east-1"
gateway_url: "https://<alias>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
knowledge_base_id: "<kbid>"      # mirrored into main.py literal for setup_permissions.py
memory_id: "<mem_id>"            # mirrored into main.py literal for setup_permissions.py
retrieval:
  top_k: 5
  chunk_separator: "\n---\n"
loyalty:
  earn_rates:   {standard: 1, device: 2, fresh: 5}
  tier_rates:   {Silver: 0.00, Gold: 0.10, Platinum: 0.15}
  points_per_dollar: 100          # 100 pts = $1
  min_redemption: 500
  max_redeem_fraction: 0.5        # points cap = 50% of order
browser: { session_timeout: 3600 }
memory:
  retrieve_top_k: 5
timeouts: { gateway_connect_seconds: 30 }
system_prompt_path: "system_prompt.txt"
```

- `REGION`, `KB_ID`, `MEMORY_ID` remain literals in `main.py` **and** mirror `config.yaml`. A
  tiny, idempotent `sync_config.py` (our tooling, not the untouchable script) rewrites just those
  three literals in `main.py` from `config.yaml` so the two never drift. Running it twice is a
  no-op.
- `GATEWAY_URL`, model id, loyalty rates, prompts, top_k, timeouts come **only** from config
  (not read by `setup_permissions.py`), so no duplication.
- `load_config()` precedence: `config.yaml` value → built-in default constant → (for the three
  mirrored IDs) the `main.py` literal. Missing/garbage YAML never crashes startup.
- The system prompt lives in `system_prompt.txt` (loaded via `system_prompt_path`), not inline.
- Secrets (AWS creds) only ever come from the environment/`.env`, never config files or code.

### Error handling & resilience

- **Tool boundary**: every `@tool` and the entrypoint wrap their body in try/except and return a
  readable string; a tool failure degrades that capability, never crashes the turn/runtime.
- **Graceful degradation**: unconfigured KB → "not configured"; Code Interpreter down → tier-only
  fallback; memory errors → logged and swallowed; gateway unreachable → agent still answers with
  local tools.
- **No bare `except: pass`** — always `except <Specific/Exception> as e:` with a log line.
- **Timeouts** on network operations where the SDK allows (gateway connect, browser session).

### Idempotency

- `sync_config.py` and all provisioning scripts are **idempotent**: re-running creates nothing
  duplicate. Lambda/API/S3 scripts use "describe-or-create" (check existence by name/ARN before
  create; update in place if present). Deterministic resource names (e.g. `order-tracker`) make
  re-runs safe. `setup_permissions.py` already uses `put_role_policy` (idempotent by design).
- The agent itself is stateless per invocation except for AgentCore-managed memory; replaying the
  same payload is safe (read-only tools) — the only state-changing tool is `initiate_refund`,
  which the model calls only on explicit user intent.

### Observability

- Module logger `CSAI_Agent` (already present); structured-ish log lines at INFO for each tool
  call (tool name + key args, no secrets/PII values) and WARNING/ERROR on failures with context.
- The entrypoint logs `actor_id`/`session_id` (identifiers, not PII) per request for traceability.

### Code quality / linting

- Lint & format with **Ruff** (lint + formatter). Add `ruff.toml` (line length, import order,
  pyflakes/pycodestyle/bugbear rules). Target: zero Ruff errors in `main.py` and tooling.
- Type hints on all public functions; `from __future__ import annotations` where helpful.
- Docstrings on every tool (also required by the rubric) and on helpers.
- Keep functions small and single-purpose; shared logic (message text extraction, config access)
  factored into named helpers.
- `python -m py_compile` + `ruff check` gate every wave before hand-off.

### Security

- NONE-authorizer Gateway is sandbox-only; documented and deleted at teardown; no sensitive data
  sent through it.
- No secrets in code, config, logs, or the committed repo (`.env` gitignored).
- Least-privilege IAM via the provided `setup_permissions.py` inline policy (KB Retrieve, scoped
  memory actions, browser session actions only).
- Input validation at the entrypoint (require `prompt`; coerce/guard types).

### Testing gate per wave

- Static: `py_compile` + `ruff check` clean.
- Local smoke where possible (config-independent waves).
- Deployed functional test for the capability the wave adds (once its resource exists).

### Documentation model (three docs, lock-step with the build)

- **Root `README.md`** — upstream course brief; untouched except a top pointer banner to
  `starter/README.md` and `DEPLOYMENT.md`. It is the reference, not the implementation guide.
- **`starter/README.md`** — developer guide for this implementation: venv setup, the
  `config.yaml` + `sync_config.py` single-source-of-truth model (and why three literals remain in
  `main.py`), running Ruff, the wave structure, deploy/permissions steps, pointer to `.kiro/specs/`.
- **`DEPLOYMENT.md`** (root) — living operational/build log: environment facts, pinned SDK
  versions and the SDK-surface verification, config architecture, infra provisioning
  commands/click-paths with the resulting resource IDs, deploy output, the six test captures, and
  teardown. Updated at **every wave hand-off** with what was actually built and verified.
- `.kiro/specs/` holds the why/what (requirements, design, tasks). `DEPLOYMENT.md` and
  `starter/README.md` hold the how/operational. No secrets in any doc.
- **`docs/ARCHITECTURE.md`** — human-facing rendered diagrams (Mermaid) + prose, the richer
  companion to the ASCII diagram in this design. Contents: (a) system architecture, (b) single
  `invoke` request flow, (c) tool-routing decision flow, (d) cross-session memory sequence (T4),
  (e) deployment/provisioning order (scriptable vs console), (f) configuration flow. Mermaid is
  chosen because it is text (diff-friendly, no binaries) and renders natively on GitHub — no local
  toolchain required. Cross-linked from both READMEs. Kept consistent with the code as it evolves.

## Architecture

```
          ┌──────────────────────────── AgentCore Runtime ────────────────────────────┐
client ── │ @app.entrypoint invoke(payload)                                            │
invoke    │   payload: {prompt, customer_id (actorId), session_id?}                    │
          │                                                                             │
          │   1. actor_id, session_id (uuid fallback)                                   │
          │   2. MemoryHook(actor_id, session_id, memory_client, MEMORY_ID)             │
          │   3. AgentCoreBrowser(region=REGION) → browser tool                         │
          │   4. with MCPClient(streamable_http_client(GATEWAY_URL)) as mcp:            │
          │        gateway_tools = mcp.list_tools_sync()                                │
          │        agent = Agent(model, tools=[kb, loyalty, browser, *gateway_tools],   │
          │                      hooks=[MemoryHook], system_prompt=...)                 │
          │        result = agent(prompt)                                               │
          │   5. return result text (first content block)                               │
          └───────┬───────────────┬──────────────┬───────────────┬────────────────────┘
                  │               │              │               │
      Bedrock KB Retrieve   Code Interpreter  AgentCore       AgentCore Gateway (MCP, NONE auth)
    (bedrock-agent-runtime) (code_session)    Browser         ├─ order_tracker  (API GW → Lambda)
                  │                                            └─ refund_processor (Lambda target)
           AgentCore Memory (MemoryClient): retrieve_memories / create_event
```

## Component and interface design

### TODO 1 — App init
```python
app = BedrockAgentCoreApp()
os.environ["BYPASS_TOOL_CONSENT"] = "true"
```
`app` must be module-level and unique. `__main__` runs `app.run()` (already in the starter).

### TODO 2 — Configuration (externalized; three literals mirrored for the permissions script)

`config.yaml` is the source of truth (see Engineering standards). `main.py` keeps exactly three
**literals** because `setup_permissions.py` AST-reads them — `REGION`, `KB_ID`, `MEMORY_ID`:

```python
# Mirrored from config.yaml by sync_config.py (do not hand-edit — edit config.yaml then sync).
REGION    = "us-east-1"                 # literal (read by setup_permissions.py)
KB_ID     = "XXXXXXXXXX"                # literal (read by setup_permissions.py)
MEMORY_ID = "CustomerSupportMemory-XXXXXXXXXX"  # literal (read by setup_permissions.py)

_CFG        = load_config()             # safe-fallback YAML loader
GATEWAY_URL = _CFG.get("gateway_url", "<gateway_url>")   # not read by setup_permissions.py
```

`load_config()` reads `config.yaml` (via PyYAML, already a dependency) and returns a dict with
built-in defaults merged under the file values; a missing/invalid file yields defaults so startup
never fails. `sync_config.py` (our idempotent tool) rewrites only the three literal lines above
from `config.yaml`, keeping the single source of truth without breaking the untouchable script.

### TODO 3 — Model and clients (values from config)
```python
model = BedrockModel(model_id=_CFG["model_id"])            # global.amazon.nova-2-lite-v1:0
memory_client = MemoryClient(region_name=REGION)
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
```
`model_id` comes from config (default `global.amazon.nova-2-lite-v1:0`); `REGION` is the mirrored
literal, which also equals `_CFG["region"]`.

### TODO 4 — `get_namespaces`
```python
def get_namespaces(mem_client, memory_id) -> Dict:
    strategies = mem_client.get_memory_strategies(memory_id)
    out = {}
    for s in strategies:
        s_type = s.get("type") or s.get("memoryStrategyType")
        templates = s.get("namespaceTemplates") or s.get("namespaces") or []
        if s_type and templates:
            out[s_type] = templates[0]
    return out
```
Returns `{"SEMANTIC": "cs_agent/{actorId}/facts", "USER_PREFERENCE": "cs_agent/{actorId}/preferences"}`.
Handles both `namespaceTemplates` (current) and legacy `namespaces`, and both `type` key variants.

### TODO 5 — `MemoryHook`
Responsibilities and key logic:

- `__init__`: store the four attributes; `self.namespaces = get_namespaces(memory_client, memory_id)`.
- `retrieve_customer_context(event: MessageAddedEvent)`:
  - `messages = event.agent.messages`; take the last message.
  - Guard: only proceed if `role == "user"` AND the content is plain text (skip tool-result blocks,
    i.e. content items containing `toolResult`).
  - Extract the query text from the text content block.
  - For each `(strategy_type, namespace)` in `self.namespaces`:
    `self.memory_client.retrieve_memories(memory_id=..., namespace=namespace.format(actorId=self.actor_id), query=query, top_k=5)`.
    Collect `m["content"]["text"]` (guarding shape) for non-empty results, tagged `[{strategy_type}]`.
  - If any memories: rewrite the last message's text block to
    `f"Customer Context:\n{joined_memories}\n\n{query}"`.
  - Wrap in try/except → log and return (never break the turn).
- `save_support_interaction(event: AfterInvocationEvent)`:
  - Walk `event.agent.messages` backwards; capture the last assistant text response and the last
    user plain-text query (skip tool-result/tool-use messages).
  - If both found: `self.memory_client.create_event(memory_id=..., actor_id=..., session_id=...,
    messages=[(query, "USER"), (response, "ASSISTANT")])`.
  - try/except → log and continue.
- `register_hooks(registry)`:
  ```python
  registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
  registry.add_callback(AfterInvocationEvent, self.save_support_interaction)
  ```

Text extraction helper (shared idea): a message's `content` is a list of blocks; a text block is
`{"text": "..."}`, a tool result is `{"toolResult": {...}}`, a tool use is `{"toolUse": {...}}`.
"Plain-text user message" = role `user` and at least one `text` block and no `toolResult` block.

### TODO 6 — `search_knowledge_base`
```python
@tool
def search_knowledge_base(query: str) -> str:
    if not KB_ID or KB_ID.startswith("<"):
        return "Knowledge base not configured."
    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
    except Exception as e:
        return f"Knowledge base search failed: {e}"
    results = resp.get("retrievalResults", [])
    if not results:
        return "No relevant information found in the knowledge base."
    chunks = [r.get("content", {}).get("text", "") for r in results]
    return "\n---\n".join(c for c in chunks if c)
```

### TODO 7 — `calculate_loyalty_discount`
Build a self-contained code string (f-string injecting the args **and the config-driven rates**),
run it in the sandbox, return the first result event as JSON. The rates and knobs come from
`_CFG["loyalty"]` (no magic numbers in code); they are injected as literals into the sandbox string
so the sandbox stays self-contained:

```
# rates/knobs injected from config.yaml -> _CFG["loyalty"]
earn_rates         = {injected}   # standard=1, device=2, fresh=5
tier_rates         = {injected}   # Silver=0.00, Gold=0.10, Platinum=0.15
points_per_dollar  = {injected}   # 100
min_redemption     = {injected}   # 500
max_redeem_fraction= {injected}   # 0.5

usable_points   = (loyalty_points // min_redemption) * min_redemption   # floor to min_redemption
max_point_value = order_total * max_redeem_fraction                      # points cap (50% of order)
point_value     = min(usable_points / points_per_dollar, max_point_value)
points_redeemed = int(round(point_value * points_per_dollar))            # points actually spent
subtotal_after_points = order_total - point_value
tier_discount   = round(subtotal_after_points * tier_rates.get(tier, 0.0), 2)
final_total     = round(subtotal_after_points - tier_discount, 2)
total_savings   = round(order_total - final_total, 2)
points_earned   = int(final_total * earn_rates.get(product_category, 1))
remaining_points = loyalty_points - points_redeemed + points_earned
print(json.dumps({...}))   # all required fields
```

The fallback path uses the same `_CFG["loyalty"]["tier_rates"]` for consistency (no duplicated
literals).

Execution + fallback (response shape **verified live**):
```python
code = f'''import json\n...<algorithm with injected literals>...'''
try:
    with code_session(REGION) as cs:
        resp = cs.invoke("executeCode", {"language": "python", "clearContext": True, "code": code})
        # resp = {"ResponseMetadata":..., "sessionId":..., "stream": <iterable>}
        # event = {"result": {"content":[{"type":"text","text":"<stdout JSON>"}],
        #                     "structuredContent":{"stdout":..., "stderr":..., "exitCode":0},
        #                     "isError": false}}
        for event in resp["stream"]:
            return json.dumps(event["result"])   # first result event
except Exception:
    # Fallback: tier discount only, no sandbox
    rate = {"Silver":0.0,"Gold":0.10,"Platinum":0.15}.get(tier,0.0)
    discount = round(order_total*rate,2)
    return json.dumps({"tier": tier, "tier_discount_rate": rate,
                       "tier_discount": discount,
                       "final_total": round(order_total-discount,2),
                       "note": "Code Interpreter unavailable; tier discount only."})
```
The agent receives the JSON `result` and reads the inner `content[0].text` (the printed JSON) or
`structuredContent.stdout`. The fallback also returns the rubric-required fields where computable.

For the T5 check (Gold, 4250 pts, $150 standard):
`usable=4000`, point value capped at `min(40, 75)=40`, subtotal after points `110`,
tier 10% → `11` off, final `99.00`, savings `51.00`, points spent `4000`, earned `99`,
remaining `4250-4000+99 = 349`.

### TODO 8 — `invoke` entrypoint
```python
@app.entrypoint
async def invoke(payload, context=None):
    try:
        user_input = payload.get("prompt")
        if not user_input:
            return "Error: 'prompt' is required."
        actor_id  = payload.get("customer_id") or "anonymous"
        session_id = payload.get("session_id") or str(uuid.uuid4())

        memory_hook = MemoryHook(actor_id, session_id, memory_client, MEMORY_ID)
        browser = AgentCoreBrowser(region=REGION)

        tools = [search_knowledge_base, calculate_loyalty_discount, browser.browser]

        mcp_client = MCPClient(lambda: streamable_http_client(GATEWAY_URL))
        with mcp_client:
            tools.extend(mcp_client.list_tools_sync())
            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=SYSTEM_PROMPT,
            )
            result = agent(user_input)
        return result.message["content"][0]["text"]
    except Exception as e:
        logger.exception("invoke failed")
        return f"Error handling request: {e}"
```

`browser.browser` is the Strands tool exposed by `AgentCoreBrowser`; confirmed present as the
`.browser` attribute in `strands-agents-tools 0.8.12`
(`AgentCoreBrowser(region=..., identifier=None, session_timeout=3600)`). Response extraction uses
the Strands `AgentResult.message` content blocks; the first text block is returned.

### System prompt (design intent)

A concise prompt that: states the agent is an Amazon store support assistant; lists the
capabilities (orders & refunds via gateway tools, product/policy via `search_knowledge_base`,
loyalty math via `calculate_loyalty_discount`, live web via the browser tool); instructs it to use
the knowledge base for policy/product/loyalty-program questions rather than guessing; to call the
loyalty calculator for any discount math; to use the customer's `customer_id` when calling order
tools; and to be concise and friendly.

## Data flow per test

- **T1/T2 (gateway)**: model selects `order_tracker___get_order` / `refund_processor___initiate_refund`;
  MCP routes to API Gateway/Lambda; tool result summarized back to the customer.
- **T3 (RAG)**: model calls `search_knowledge_base("Platinum loyalty tier benefits")`; Retrieve API
  returns the loyalty section; model answers from it.
- **T4 (memory)**: session A writes facts/preferences via `create_event`; async extraction populates
  the strategy namespaces; session B's `retrieve_customer_context` finds "Jane" + "concise" and
  prepends them so the model recalls.
- **T5 (code)**: model calls `calculate_loyalty_discount(4250, "Gold", 150, "standard")`.
- **T6 (browser)**: model calls the browser tool to fetch the page title.

## Error handling

- Every tool and the entrypoint catch exceptions and return readable strings; the agent turn never
  crashes the runtime.
- Memory failures are logged and swallowed so a cold/propagating memory resource does not block
  answers.
- KB and Code Interpreter have explicit "not configured"/fallback paths.

## Testing strategy

- **Local smoke** (optional): temporarily switch the `__main__` block to `main()` and run
  `python main.py '{"prompt":"...","customer_id":"CUST-123","session_id":"s1"}'` (in the activated
  venv) once config is set. Revert to `app.run()` before deploying.
- **Deployed functional tests**: the six `agentcore invoke` scenarios in Requirement 9; capture
  terminal output for each.
- **Permissions**: `python setup_permissions.py --dry-run` first to confirm config literals parse,
  then the real run after deploy.
- No automated unit tests are added (not requested); correctness is verified against the six
  scenarios and the T5 arithmetic shown above.

## Infrastructure provisioning (prerequisite — produces the four config IDs)

The agent code depends on AWS resources that this project provisions **100% as code — no console
(UI) steps**. The system `aws` CLI (2.27.33) lacks the AgentCore control plane, but the **venv
boto3 (1.43.123) has every required create *and* delete operation** (verified): `bedrock-agentcore-control`
(`CreateGateway`/`CreateGatewayTarget`/`CreateMemory` + deletes), `bedrock-agent`
(`CreateKnowledgeBase`/`CreateDataSource`/`StartIngestionJob` + deletes), `opensearchserverless`
(collection + security/network/data-access policies + deletes), and `lambda`/`apigateway`/`s3`/`iam`.
The agent **Runtime** is provisioned via the sanctioned toolkit (`agentcore configure`/`deploy`/
`destroy`) — which is also code/CLI, not UI.

### IaC approach — Option A: idempotent boto3 orchestrator (`infra/`)

A small Python package `starter/infra/` using the venv boto3 and the `.env` credentials. Design
principles: **idempotent** (describe-or-create by deterministic name; update-in-place; re-run is a
no-op), **ordered** (respects dependencies), **reversible** (symmetric destroy), and **stateful
enough** to discover existing resources (tags + deterministic names; a small `infra/state.json`
records created IDs/ARNs for clean teardown and to emit the config values).

```
starter/infra/
  __init__.py
  common.py        # session from .env, tagging, waiters, describe-or-create helpers, logging
  iam.py           # lambda exec role, KB service role (least-privilege)
  lambdas.py       # zip + create/update order-tracker, refund-processor (Python 3.12)
  apigw.py         # REST API + 3 resources + GET Lambda-proxy methods (get_order,...) + deploy prod
  s3_catalog.py    # bucket + upload product_catalog.txt
  opensearch.py    # Serverless collection + encryption/network/data-access policies + vector index
  knowledge_base.py# KB (managed, OSS backing) + data source + StartIngestionJob + wait COMPLETE
  gateway.py       # Gateway (NONE auth) + target order_tracker (API GW, 3 ops) + refund_processor (Lambda, lambda_schema)
  memory.py        # Memory + 2 strategies (customer_facts, customer_preferences); wait ACTIVE
  provision.py     # `python -m infra.provision`  → create all, in order, write config values
  destroy.py       # `python -m infra.destroy`    → delete all, reverse order, idempotent
  outputs.py       # writes GATEWAY_URL/KB_ID/MEMORY_ID into config.yaml, then runs sync_config.py
```

| Layer | Resource | boto3 service | Produces | Idempotency key |
|---|---|---|---|---|
| 1 | IAM roles (lambda exec, KB service) | `iam` | role ARNs | role name |
| 2 | `order-tracker`, `refund-processor` Lambdas (Py 3.12) | `lambda` | function ARNs | function name |
| 3 | REST API + `/orders/{order_id}`, `/customers/{customer_id}/orders`, `/customers/{customer_id}` GET proxy (ops `get_order`/`get_customer_orders`/`get_customer`), stage `prod` | `apigateway` | REST API id, stage | API name (tag) |
| 4 | S3 bucket + `product_catalog.txt` | `s3` | bucket/key | bucket name |
| 5 | OpenSearch Serverless collection + policies + index | `opensearchserverless` | collection ARN/endpoint | collection name |
| 6 | Managed KB + data source + ingestion | `bedrock-agent` | **`KB_ID`** | KB name |
| 7 | Gateway (NONE auth) + 2 targets | `bedrock-agentcore-control` | **`GATEWAY_URL`** | gateway name |
| 8 | Memory + 2 strategies | `bedrock-agentcore-control` | **`MEMORY_ID`** | memory name |

After layers 6–8, `outputs.py` writes `GATEWAY_URL`, `KB_ID`, `MEMORY_ID` into `config.yaml` and
runs `sync_config.py` to mirror the three literals into `main.py` — so provisioning feeds config
with **zero manual editing**.

### Full lifecycle (all code, copy-paste)

```bash
cd starter && source .venv/bin/activate && set -a; source ../.env; set +a

python -m infra.provision          # layers 1-8, idempotent; writes config.yaml + syncs main.py
agentcore configure --entrypoint main.py --name <name> \
    --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory
agentcore deploy                   # Runtime (code path; Docker UP)
python setup_permissions.py        # least-privilege policy on the runtime role
# run the six tests ...
agentcore destroy                  # tear down Runtime
python -m infra.destroy            # tear down layers 8-1 in reverse (Gateway, Memory, KB, OSS, S3, APIGW, Lambdas, IAM)
```

### Verification (also code, no UI)

- `aws sts get-caller-identity` (or boto3 equivalent) for identity.
- Gateway tools: a tiny boto3/`MCPClient` probe lists the tools (no MCP Inspector UI needed);
  optional `npx @modelcontextprotocol/inspector` remains available but is not required.
- KB: a `bedrock-agent-runtime.retrieve` call for "return policy for electronics" asserts the
  15-day result.
- Memory: `get_memory` shows `ACTIVE` with two strategies.

### Risks specific to all-code provisioning

- **KB/OpenSearch is the most multi-step layer** (collection + 3 policy types + vector index + KB
  + data source + ingestion). It is built with explicit waiters and idempotent guards; the vector
  index creation uses the OSS data-plane endpoint. This is the layer most likely to need a retry;
  `provision.py` is safe to re-run.
- **Eventual consistency / waiters**: Memory→ACTIVE, ingestion→COMPLETE, IAM propagation, and
  collection→ACTIVE all use polling waiters with timeouts (no fixed sleeps where a waiter exists).
- **Runtime stays on the toolkit**: `agentcore deploy` is the supported path and produces
  `.bedrock_agentcore.yaml` that `setup_permissions.py` reads; re-implementing it via
  `CreateAgentRuntime` directly is intentionally avoided to not fight the project workflow.

## Deployment workflow

This machine uses a **venv + pip** (not `uv`). Activate the venv and load creds in each shell:

```bash
cd starter
python3 -m venv .venv && source .venv/bin/activate
pip install .                      # from pyproject.toml
set -a; source ../.env; set +a     # temporary lab AWS creds

python -m infra.provision          # all infra as code; writes config.yaml + syncs main.py literals
agentcore configure --entrypoint main.py --name <name> \
    --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory
agentcore deploy                   # requires Docker running (confirmed UP)
python setup_permissions.py        # grant KB + memory + browser
# wait briefly for IAM propagation, then run the six tests
```

The system `aws` CLI cannot create AgentCore Gateway/Memory/Runtime (service missing in CLI
2.27.33); those go through the toolkit above or the AWS console. Gateway targets and the Managed
Knowledge Base are console-driven prerequisites.

## Optional enhancement (R11.1) — Pydantic validation

Lowest-risk stand-out: define a `DiscountBreakdown` Pydantic model and validate the Code
Interpreter output (parse `content[0].text`) before the tool returns, re-serializing the validated
model. This is additive, does not change the tool signature, and is layered on only after the six
tests pass. It is omitted if it risks any baseline criterion.

## Risks and mitigations

- **SDK surface drift**: RESOLVED — browser `.browser` tool, code-interpreter `resp["stream"]` →
  `event["result"]` shape, and memory `get_memory_strategies`/`retrieve_memories`/`create_event`
  signatures were all verified against the installed versions (one live Code Interpreter call).
  Memory-record and retrieve-result dict shapes are still read defensively (`.get(...)`).
- **Docker dependency** for `agentcore deploy`: confirmed Docker is UP on this machine.
- **Memory extraction latency** (T4): the test instructions already require a ~30s wait; document it.
- **NONE authorizer**: acceptable only for this sandbox; the design notes it must be deleted at
  cleanup and never used for sensitive data.
- **Model access**: Nova 2 Lite must be enabled in the account, else invocations fail with an
  access error — a prerequisite, not a code issue.
