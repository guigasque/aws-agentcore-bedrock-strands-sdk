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
| Exact loyalty discounts | `calculate_loyalty_discount` → Code Interpreter | 🔜 Wave 3 |
| Live web browsing | AgentCore Browser tool | 🔜 Wave 4 |
| Order tracking & refunds | AgentCore Gateway (MCP) → Lambda/API Gateway | 🔜 Wave 5 |
| Cross-session memory | `MemoryHook` → AgentCore Memory | 🔜 Wave 6 |

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

### 3.2 Exact loyalty discounts — Code Interpreter 🔜 (Wave 3)

**What (planned):** compute points redeemed, tier discount, final total, and remaining points
*exactly*, instead of trusting the model's mental arithmetic.

**How (planned):** `calculate_loyalty_discount(...)` builds a self-contained Python snippet encoding
the loyalty rules (earn rates, tier rates, 500-point floor, 50%-of-order cap — all from
`config.yaml`), runs it in the AgentCore **Code Interpreter** sandbox, and returns a structured
result. If the sandbox is unavailable, it falls back to a tier-only calculation (never raises).

### 3.3 Live web browsing — Browser tool 🔜 (Wave 4)

**What (planned):** open a live URL and report real-time information (e.g., a page title).

**How (planned):** an AgentCore Browser tool is added to the agent. Note: the starter's
`AgentCoreBrowser` was removed in `strands-agents-tools` 0.9.0, so we pin `<0.9.0` and load the
tool lazily; Wave 4 decides whether to stay pinned or migrate to `BrowserClient` + Playwright.

### 3.4 Orders & refunds — AgentCore Gateway (MCP) 🔜 (Wave 5)

**What (planned):** track orders, look up customers, initiate refunds, generate return labels.

**How (planned):** two Lambda functions (`order-tracker` via API Gateway, `refund-processor` as a
direct Lambda target) are exposed through the AgentCore **Gateway** as MCP tools. `invoke()` opens
an `MCPClient` to the gateway's `/mcp` endpoint, loads the tools, and adds them to the agent — so
the model can call them like any local tool. The gateway uses the NONE authorizer (sandbox only).

### 3.5 Cross-session memory — MemoryHook 🔜 (Wave 6)

**What (planned):** remember a customer's name and preferences across *separate* sessions.

**How (planned):** a `MemoryHook` (a Strands `HookProvider`) registers two callbacks —
`retrieve_customer_context` (before the model answers, pulls facts/preferences from AgentCore Memory
and prepends them) and `save_support_interaction` (after answering, persists the turn). AgentCore
Memory extracts facts/preferences asynchronously into per-customer namespaces, so a later session
for the same `customer_id` recalls them (Test T4). See [`ARCHITECTURE.md` (d)](ARCHITECTURE.md).

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
