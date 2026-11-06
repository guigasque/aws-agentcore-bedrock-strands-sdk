# How It Works — Customer Support AI Agent

The single "start here to understand the system" document. It explains what the agent is, how one
request flows through it end to end, how each capability works, and how configuration and
infrastructure tie together. Visual companions live in [`ARCHITECTURE.md`](ARCHITECTURE.md); the
build-time spec is [`../.kiro/specs/customer-support-agent/design.md`](../.kiro/specs/customer-support-agent/design.md);
how to run it is [`../starter/README.md`](../starter/README.md); the operational log is
[`../DEPLOYMENT.md`](../DEPLOYMENT.md).

> **Status legend:** ✅ implemented & verified · 🔜 planned (wave noted). This doc is kept in
> lock-step with the build — sections are fleshed out as their wave lands.

| Capability | Mechanism | Status |
|---|---|---|
| Deployable cloud agent | `BedrockAgentCoreApp` + `@app.entrypoint`, Nova 2 Lite | ✅ Wave 1 |
| Product/policy answers (RAG) | `search_knowledge_base` → Bedrock KB (S3 Vectors) | ✅ Wave 2 |
| Exact loyalty discounts | `calculate_loyalty_discount` → Code Interpreter | ✅ Wave 3 |
| Live web browsing | AgentCore Browser tool | ✅ Wave 4 |
| Order tracking & refunds | AgentCore Gateway (MCP) → Lambda/API Gateway | ✅ Wave 5 |
| Cross-session memory | `MemoryHook` → AgentCore Memory | ✅ Wave 6 |

All six capabilities are implemented and cloud-verified (tests T1–T6 all passing).

---

## 1. The mental model

The agent is a single **Strands `Agent`** powered by **Amazon Nova 2 Lite**, served by the
**Amazon Bedrock AgentCore Runtime**. A client sends a JSON payload (`prompt`, `customer_id`,
`session_id`) to the runtime; the runtime calls one function — `invoke()` — which builds the agent
for that request, lets the model reason and call tools, and returns the text answer.

Everything the agent can *do* is a **tool** the model may choose to call: search the knowledge
base, calculate a discount, browse the web, or hit order/refund tools over MCP. A **memory hook**
wraps each turn to recall and persist customer context. The model decides which tool to use based
on the system prompt and each tool's docstring.

Design choices that shape the code (details in `design.md`):
- **Per-invocation agent** — tools (especially the MCP gateway connection) are only valid inside
  their context, and memory is scoped to the request's `customer_id`/`session_id`, so the agent is
  assembled fresh each call.
- **Lazy, crash-proof startup** — the model/clients are created on first use and heavy/optional
  imports happen inside the functions that use them, so module import always finishes within the
  runtime's init window (learned the hard way in Wave 1).
- **Config is externalized** — all tunables live in `config.yaml` (single source of truth).

---

## 2. One request, end to end ✅ (Wave 1 core; tools/memory fill in per wave)

Trace a single `agentcore invoke '{"prompt": "...", "customer_id": "CUST-123", "session_id": "s1"}'`.
See [`ARCHITECTURE.md` (b)](ARCHITECTURE.md) for the sequence diagram.

1. **Runtime receives the request.** `BedrockAgentCoreApp` (an ASGI app) routes it to the
   `@app.entrypoint async def invoke(payload, context)`.
2. **Input validation & identity.** `invoke()` requires a non-empty `prompt`; derives `actor_id`
   from `customer_id` (default `anonymous`) and `session_id` (a generated UUID if absent); logs the
   identifiers (not PII values) for traceability.
3. **Memory hook built** 🔜 Wave 6 — a `MemoryHook` for this actor/session is attached so context
   can be recalled before the model answers and the turn saved after.
4. **Tools assembled** — the tool list is built: `search_knowledge_base` ✅, plus (as waves land)
   `calculate_loyalty_discount` 🔜, the browser tool 🔜, and the gateway MCP tools 🔜 loaded inside
   the MCP client context.
5. **Context retrieval** 🔜 Wave 6 — before the model sees the message, the hook queries AgentCore
   Memory and prepends any recalled facts/preferences as a "Customer Context:" preamble.
6. **The model reasons** — Nova 2 Lite reads the (possibly enriched) prompt, the system prompt, and
   the tool docstrings, and decides whether to answer directly or call a tool.
7. **Tool execution** — if a tool is called, it runs (e.g. a KB retrieve), returns a result, and the
   model incorporates it. Tools degrade gracefully (readable errors) and never crash the turn.
8. **Response** — the first text block of the model's answer is extracted and returned to the client.
9. **Persist the turn** 🔜 Wave 6 — after answering, the hook saves `(user, assistant)` to memory
   for future recall.

Today (Waves 1–2) a prompt like "What are the Platinum tier benefits?" exercises steps 1–2, 4, 6–8:
the model calls `search_knowledge_base`, gets grounded chunks, and answers.

---

## 3. How each capability works

### 3.1 Grounded answers — RAG ✅ (Wave 2)

**What:** product specs, return/refund policy, warranty, loyalty rules, order-status definitions,
and product recommendations are answered from the catalog, not from the model's guesses.

**How:** `search_knowledge_base(query)` calls the Bedrock Knowledge Base **Retrieve API**, which
semantically matches the question against embedded chunks and returns the relevant text; the model
answers from those chunks. The tool guards an unconfigured KB, joins chunks with a configured
separator, and honors a configured `top_k`.

**Where the data comes from:** the KB's only source is `starter/product_catalog.txt`. Provisioning
uploads it to an S3 source bucket; an ingestion job **chunks** it, **embeds** each chunk with
Amazon Titan Text Embeddings v2, and stores the vectors in an **S3 Vectors** index. The KB is a
**snapshot** — editing the catalog requires re-running `python -m infra.provision_kb` to re-ingest.
(S3 Vectors was chosen over OpenSearch Serverless: simpler as code and no always-on cost.) See
[`ARCHITECTURE.md` (c2)](ARCHITECTURE.md).

**Verified:** retrieving "return policy for electronics" returns the "15 days" chunk; the agent
answers the Platinum-tier question with free same-day shipping, 15% discount, and priority support
(Test T3).

### 3.2 Exact loyalty discounts — Code Interpreter ✅ (Wave 3)

**What:** compute points redeemed, tier discount, final total, and remaining points *exactly*,
instead of trusting the model's mental arithmetic.

**How:** `calculate_loyalty_discount(...)` builds a self-contained Python snippet encoding the
loyalty rules (earn rates, tier rates, 500-point floor, 50%-of-order cap — all from `config.yaml`),
runs it in the AgentCore **Code Interpreter** sandbox via `code_session(REGION).invoke("executeCode",
...)`, and returns a structured JSON result. If the sandbox is unavailable it falls back to a
tier-only calculation (never raises).

**Permissions note:** the Code Interpreter needs `bedrock-agentcore:*CodeInterpreter*` on the
runtime role. The provided `setup_permissions.py` does not grant this (and is gated on Memory), so
`infra/agent_permissions.py` grants it separately (idempotent).

**Verified (cloud, T5):** Gold / 4250 pts / $150 standard → 4,000 points redeemed, 10% tier
discount ($11), **final $99.00**, $51 saved, 99 earned, 349 remaining — from the live sandbox, not
the fallback.

### 3.3 Live web browsing — Browser tool ✅ (Wave 4)

**What:** open a live URL and report real-time information (e.g., a page title).

**How:** `_browser_tools()` lazily imports `strands_tools.browser.AgentCoreBrowser`, instantiates it
with `region` + `session_timeout` from config, and adds its `.browser` tool to the agent. The tool
is session-based (the model runs `init_session` → `navigate` → `get_text`/title) and drives a
headless browser in an AgentCore browser session. If the import is unavailable it logs and returns
`[]` (graceful — the agent still runs without browsing).

**Version caveat:** `strands_tools.browser` was removed in `strands-agents-tools` 0.9.0, so
`pyproject.toml` pins `<0.9.0`. The import is lazy so a future drift can't crash module import.

**Permissions note:** needs `bedrock-agentcore:*BrowserSession*` / stream actions on the runtime
role, granted by `infra/agent_permissions.py` (setup_permissions.py grants these too but is gated on
Memory).

**Verified (cloud, T6):** "Go to https://www.udacity.com and tell me the page title" →
*"Learn the Latest Tech Skills; Advance Your Career | Udacity"* (live fetch).

### 3.4 Orders & refunds — AgentCore Gateway (MCP) ✅ (Wave 5)

**What:** track orders, look up customers, initiate refunds, generate return labels.

**How:** two Lambda functions — `order-tracker` (fronted by an API Gateway REST stage) and
`refund-processor` (a direct Lambda target) — are exposed through the AgentCore **Gateway** as MCP
tools. `invoke()` opens an `MCPClient` to the gateway's `/mcp` endpoint (lazy import, graceful
fallback to local tools if unreachable), loads the tools inside the client context, and creates +
runs the `Agent` there (gateway tools are only valid while the context is open). Tool names are
prefixed by target, e.g. `order-tracker___get_order`, `refund-processor___initiate_refund`. The
gateway uses the NONE authorizer (sandbox only) so the unsigned MCP client connects directly.

**Infra (all code):** `infra/lambdas.py` (deploy both Lambdas), `infra/apigw.py` (REST API + 3 GET
proxy routes + method responses, stage `prod`), `infra/gateway.py` (Gateway + 2 targets, with
`toolFilters` for the API routes and the `lambda_schema` inline payload for refunds). Entry point:
`python -m infra.provision_gateway`.

**Verified (cloud):** 6 MCP tools exposed (3 order + 3 refund). **T1** — "track order ORD-001" →
SHIPPED, TRK987654321, UPS, ETA. **T2** — "refund my Kindle (ORD-002)" → refund ID, APPROVED,
"3-5 business days". (Rubric 2: ≥2 well-formed MCP tool invocations.)

### 3.5 Cross-session memory — MemoryHook ✅ (Wave 6)

**What:** remember a customer's name and preferences across *separate* sessions.

**How:** a `MemoryHook` (Strands `HookProvider`) registers two callbacks —
`retrieve_customer_context` (on `MessageAddedEvent`: for a plain-text user message, queries each
strategy namespace via `retrieve_memories` and prepends findings as a "Customer Context:" preamble)
and `save_support_interaction` (on `AfterInvocationEvent`: walks messages backwards for the last
user+assistant pair and persists them via `create_event`). `get_namespaces()` reads the strategy →
namespace mapping (`namespaceTemplates[0]` with `namespaces[0]` fallback). AgentCore Memory extracts
facts/preferences **asynchronously** into per-customer namespaces, so a later session for the same
`customer_id` recalls them. All failures degrade gracefully (logged, turn continues).

**Infra (all code):** `infra/memory.py` creates `CustomerSupportMemory` with two strategies
(semantic → `cs_agent/{actorId}/facts`, user-preference → `cs_agent/{actorId}/preferences`) via
`create_or_get_memory` + waiter. Entry point: `python -m infra.provision_memory`. Because MEMORY_ID
now exists, the provided `setup_permissions.py` finally runs (KB + Memory + Browser); the Code
Interpreter grant stays in `infra/agent_permissions.py`.

**Verified (cloud, T4):** session s-A "Hi, I am Jane. I prefer concise responses." → async
extraction populated facts ("name is Jane", "prefers concise") and preferences → new session s-B2
"Do you remember my name and communication preference?" → *"Yes, Jane. I remember you prefer concise
responses."* Note: extraction is eventually-consistent (can take 60-90s+); recall only works after it
completes.

---

## 4. Configuration — single source of truth ✅

`config.yaml` holds every tunable value (model id, region, gateway URL, KB/memory IDs, retrieval
knobs, loyalty rates, timeouts, system-prompt path). `main.py` reads it via `load_config()` with
minimal safe defaults so startup never fails.

Three values — `REGION`, `KB_ID`, `MEMORY_ID` — must *also* appear as **literal** assignments in
`main.py`, because the provided `setup_permissions.py` reads them by AST-parsing literals. They are
not hand-edited: `sync_config.py` mirrors them from `config.yaml` (idempotent), and the provisioner
(`infra/outputs.py`) runs that sync automatically after creating resources. `GATEWAY_URL` is read
directly from config (the permissions script doesn't touch it). See
[`ARCHITECTURE.md` (f)](ARCHITECTURE.md).

Flow: `provision → infra/state.json → config.yaml → (sync_config.py) → main.py literals → setup_permissions.py`.

---

## 5. Infrastructure — 100% as code ✅ (KB layer built; rest in Wave I)

All AWS resources are created and destroyed by code (no console). The agent **Runtime** uses the
AgentCore toolkit (`agentcore configure/deploy/destroy` — also CLI). Everything else is an
idempotent boto3 package, `starter/infra/` (describe-or-create by deterministic name; waiters for
async states; symmetric teardown).

- **Built (Wave 2):** IAM KB role, S3 source bucket + catalog upload, S3 Vectors bucket + index,
  the Knowledge Base + data source + ingestion. Entry point: `python -m infra.provision_kb`.
- **Planned (Wave I):** Lambdas, REST API, AgentCore Gateway + targets, AgentCore Memory, and the
  unified `infra.provision` / `infra.destroy` / `infra.verify` entry points.

See [`ARCHITECTURE.md` (e)](ARCHITECTURE.md) for the provisioning order, and `DEPLOYMENT.md` for the
actual resource IDs and the build log.

---

## 6. Lifecycle at a glance

```bash
# provision infra (as code) → deploy the agent → grant permissions → verify → test → tear down
python -m infra.provision          # (today: infra.provision_kb for the KB layer)
agentcore configure --entrypoint main.py --name cs_support_agent \
    --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory
agentcore deploy
python setup_permissions.py
python -m infra.verify             # (planned)
agentcore invoke '{"prompt": "...", "customer_id": "CUST-123", "session_id": "s1"}'
agentcore destroy && python -m infra.destroy
```

---

## 7. Where to go next

- **Understand the design decisions:** `../.kiro/specs/customer-support-agent/design.md`
- **See the diagrams:** [`ARCHITECTURE.md`](ARCHITECTURE.md)
- **Run / deploy it:** [`../starter/README.md`](../starter/README.md)
- **What's been built, wave by wave:** [`../DEPLOYMENT.md`](../DEPLOYMENT.md) (§6 build log)
- **Requirements & tasks:** `../.kiro/specs/customer-support-agent/requirements.md` and `tasks.md`
