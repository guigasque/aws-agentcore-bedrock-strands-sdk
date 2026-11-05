# Requirements — Customer Support AI Agent (Amazon Bedrock AgentCore + Strands)

## Introduction

This feature is a fully functional AI customer support agent for a fictional e-commerce
platform ("Amazon store"), built with Amazon Bedrock AgentCore and the Strands SDK. The agent is a
single conversational interface that handles order tracking, returns/refund processing, product
recommendations and product/policy questions, and loyalty-rewards calculation, while maintaining
context across **complex multi-turn conversations**. It is deployed to the AgentCore Runtime via
the Python `bedrock-agentcore-starter-toolkit` (`agentcore configure`, `deploy`, `invoke`,
`destroy`).

Conversation context is maintained at two levels: within a session, AgentCore Runtime preserves
the turn history for a given `session_id` (short-term); across sessions for the same
`customer_id`, the `MemoryHook` retrieves and persists facts/preferences via AgentCore Memory
(long-term). **Product recommendations** are served through the Knowledge Base search tool
(grounded in `product_catalog.txt`), not a separate recommender.

The implementation surface is `starter/main.py`, which ships with eight numbered `# TODO`
sections (TODO 1–8). The AWS infrastructure (Lambda, API Gateway, AgentCore Gateway, Knowledge
Base, Memory) is provisioned out-of-band per the README and injected into `main.py` as four
configuration values: `GATEWAY_URL`, `KB_ID`, `REGION`, `MEMORY_ID`.

The deliverables are: a completed `main.py` with no placeholder code, terminal/screenshot output
for six test scenarios, and a 200–400 word written reflection.

### Environment notes (this machine)

- Python dependency management uses a **virtualenv** (`starter/.venv`) with `pip`, **not `uv`**.
  Every toolkit/agent command runs after `source .venv/bin/activate`; e.g. `agentcore deploy`
  (not `uv run agentcore deploy`). AWS creds are loaded from `./.env`
  (`set -a; source .env; set +a`) — temporary lab credentials that expire.
- The system AWS CLI (2.27.33) does **not** include the `bedrock-agentcore-control` service, so
  AgentCore control-plane operations (Gateway, Memory, Runtime) go through the Python starter
  toolkit or the AWS console, not the system `aws` CLI.
- Installed versions (pinned at build time): `strands-agents 1.63.0`,
  `strands-agents-tools 0.8.12`, `bedrock-agentcore 1.25.3`,
  `bedrock-agentcore-starter-toolkit 0.3.14`, `mcp 2.3.0`, `boto3 1.43.123`.

### Rubric traceability

The implementation must satisfy the official grading rubric (7 criteria). Mapping:

| Rubric criterion | Requirement(s) |
|---|---|
| 1. Deploy an AI agent to a cloud runtime | R1 (app/entrypoint/run), R9 (deploy + T1 output) |
| 2. Integrate external tools via MCP | R8 (MCPClient + load tools), R9 (T1 order + T2 refund logs) |
| 3. RAG with a knowledge base | R6 (search_knowledge_base) |
| 4. Cross-session memory | R4 (get_namespaces), R5 (MemoryHook), R9 (T4 two-session log) |
| 5. Sandboxed code interpreter | R7 (calculate_loyalty_discount) |
| 6. Web browsing via browser tool | R8 (AgentCoreBrowser + tools list), R9 (T6 live-page log) |
| 7. Reflection | R10.3 |

Rubric-exact obligations folded into the acceptance criteria below:
- MCP test log must show **≥ 2 tool invocations**, each returning a **well-formed response**
  (R8, R9: T1 and T2 cover this).
- `search_knowledge_base` must: use `@tool`, call the Retrieve API, **join** chunks into one
  formatted string, **guard** on missing `KB_ID`, and have a **docstring** stating when to use it
  (R6).
- `get_namespaces` must fetch strategy **types and namespaces**; `MemoryHook` must **extend
  HookProvider** and register via a **register method**; include `retrieve_customer_context`
  (queries **all** strategy namespaces) and `save_support_interaction` (extracts the last user
  interaction and stores it) (R4, R5).
- `calculate_loyalty_discount` must: use `@tool`, build a **self-contained** Python code string
  encoding the business logic, execute via `code_session(REGION).invoke("executeCode", ...)`,
  include a **tier-only fallback** on failure, and return a **structured result with all required
  fields** (R7).
- Browser: instantiate `AgentCoreBrowser` with the region and add it to the agent's tools list
  (R8).

### Scope boundary

- **In scope**: completing the eight TODO sections in `starter/main.py`; the deploy/permissions
  workflow; the six functional tests; the reflection.
- **Out of scope / not modified**: `starter/lambda/*` (deployed as-is), `starter/setup_permissions.py`
  (run as-is), `starter/product_catalog.txt` (uploaded as-is). Provisioning of AWS infrastructure
  through the console is a manual prerequisite, not code in this repo.
- **Reference repo** (`github.com/guigasque/aws-agentcore-support-chatbot`): used only as an
  SDD/documentation *style* reference. It is a different project (a prompt-routing chatbot) whose
  `main.py` does **not** match this starter, so it is NOT an implementation source. The
  implementation is derived from this starter's TODOs + the verified installed SDK surface.

### Fixed facts the implementation depends on (from starter artifacts)

- Model: `global.amazon.nova-2-lite-v1:0` (Amazon Nova 2 Lite global inference profile).
- Region: `us-east-1`.
- Memory strategies & namespaces: Semantic → `cs_agent/{actorId}/facts`;
  User preference → `cs_agent/{actorId}/preferences`.
- Gateway authorizer: NONE (unsigned MCP connection via `streamable_http_client`).
- Mock data (from `order_tracker.py`): CUST-123 = "Jane Smith", Gold, 4250 points;
  ORD-001 SHIPPED, TRK987654321, UPS; ORD-002 Kindle Paperwhite DELIVERED.
- Refund tool (`refund_processor.py`): `initiate_refund` returns APPROVED + "3-5 business days".
- Loyalty rules (from `product_catalog.txt`): earn rates standard=1, device=2, fresh=5 pts/$;
  100 pts = $1; min redemption 500 pts; tiers Silver=0%, Gold=10%, Platinum=15%.

---

## Requirements

### Requirement 1 — App initialization and runtime wiring (TODO 1)

**User Story:** As the AgentCore Runtime, I need a single ASGI application instance so that the
agent can be served and invoked in a headless cloud deployment.

#### Acceptance Criteria

1. WHEN `main.py` is imported THEN the system SHALL create exactly one `BedrockAgentCoreApp`
   instance bound to the name `app`.
2. WHEN the module loads THEN the system SHALL set the environment variable `BYPASS_TOOL_CONSENT`
   to `"true"` so tool calls do not block on interactive consent prompts.
3. WHEN the module is run as `__main__` THEN the system SHALL start the server via `app.run()`.

### Requirement 2 — Configuration values (TODO 2)

**User Story:** As the agent operator, I need the four AWS resource identifiers defined as literal
strings so that the agent connects to the correct Gateway, Knowledge Base, Memory, and region, and
so `setup_permissions.py` can validate them.

#### Acceptance Criteria

1. The system SHALL define `GATEWAY_URL`, `KB_ID`, `REGION`, and `MEMORY_ID` as module-level
   string literals.
2. `GATEWAY_URL` SHALL match the form
   `https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`.
3. `KB_ID` SHALL be a 10-character alphanumeric string; `REGION` SHALL match the region pattern
   (e.g. `us-east-1`); `MEMORY_ID` SHALL match `[A-Za-z][A-Za-z0-9_]{0,99}-[A-Za-z0-9]{10}`.
4. IF any value is left as its placeholder THEN `setup_permissions.py` SHALL fail with a message
   instructing the operator to fill the literal value. (This constrains the format; actual IDs are
   provided by the operator's provisioned resources.)

### Requirement 3 — Model and clients (TODO 3)

**User Story:** As the agent, I need a configured Bedrock model and the AWS clients so that I can
reason with Nova Lite and reach Memory and the Knowledge Base.

#### Acceptance Criteria

1. The system SHALL create a `BedrockModel` using `model_id = "global.amazon.nova-2-lite-v1:0"`.
2. The system SHALL create a `MemoryClient` with `region_name=REGION`.
3. The system SHALL create a `boto3` client for `"bedrock-agent-runtime"` in `REGION`, bound to
   `_bedrock_runtime`.

### Requirement 4 — Namespace helper (TODO 4)

**User Story:** As the memory hook, I need to know each strategy's namespace template so that I can
query and write memories under the correct namespaces.

#### Acceptance Criteria

1. WHEN `get_namespaces(mem_client, memory_id)` is called THEN the system SHALL call
   `mem_client.get_memory_strategies(memory_id)`.
2. For each strategy, the system SHALL read the namespace from `strategy["namespaceTemplates"][0]`
   AND SHALL fall back to `strategy["namespaces"][0]` WHEN `namespaceTemplates` is absent.
3. The system SHALL return a dict keyed by strategy type, e.g.
   `{"SEMANTIC": "cs_agent/{actorId}/facts", "USER_PREFERENCE": "cs_agent/{actorId}/preferences"}`.

### Requirement 5 — Long-term memory hook (TODO 5)

**User Story:** As a returning customer, I want the agent to remember my name and preferences
across separate sessions so that I do not have to repeat myself.

#### Acceptance Criteria

1. WHEN `MemoryHook` is constructed THEN the system SHALL store `actor_id`, `session_id`,
   `memory_id`, and `memory_client` as attributes AND SHALL set `self.namespaces` from
   `get_namespaces(...)`.
2. WHEN a plain-text user message is added (a `MessageAddedEvent`) THEN `retrieve_customer_context`
   SHALL run ONLY for user-role, non-tool-result text messages.
3. WHEN retrieving context THEN the system SHALL, for each namespace, call
   `memory_client.retrieve_memories(memory_id, namespace (with `{actorId}` substituted), query,
   top_k=5)`, collect non-empty memory texts tagged with their strategy type, and IF any are found
   THEN prepend them to the user message as
   `"Customer Context:\n<memories>\n\n<original_message>"`.
4. WHEN the agent finishes a turn (an `AfterInvocationEvent`) THEN `save_support_interaction` SHALL
   walk `event.agent.messages` backwards to find the last plain-text user query and the last
   assistant response AND SHALL call `memory_client.create_event(memory_id, actor_id, session_id,
   messages=[(query, "USER"), (response, "ASSISTANT")])`.
5. WHEN `register_hooks(registry)` is called THEN the system SHALL register
   `retrieve_customer_context` on `MessageAddedEvent` AND `save_support_interaction` on
   `AfterInvocationEvent`.
6. IF a memory call raises THEN the hook SHALL log and continue without breaking the turn.

### Requirement 6 — Knowledge Base search tool / RAG (TODO 6)

**User Story:** As a customer, I want product and policy questions answered from the grounded
catalog so that the answers are accurate and not hallucinated.

#### Acceptance Criteria

1. The system SHALL expose `search_knowledge_base(query: str) -> str` decorated with `@tool`, with
   a docstring describing when to use it.
2. IF `KB_ID` is empty/unset THEN the tool SHALL return `"Knowledge base not configured."`.
3. WHEN invoked THEN the tool SHALL call
   `_bedrock_runtime.retrieve(knowledgeBaseId=KB_ID, retrievalQuery={"text": query})`.
4. WHEN `retrievalResults` is empty THEN the tool SHALL return a no-results message; OTHERWISE it
   SHALL join the result chunk texts with `"\n---\n"` and return them.
5. IF the Retrieve API raises THEN the tool SHALL return a readable error string rather than
   raising.

### Requirement 7 — Loyalty discount calculator via Code Interpreter (TODO 7)

**User Story:** As a customer, I want my loyalty discount computed exactly so that the final price
and remaining points are correct.

#### Acceptance Criteria

1. The system SHALL expose
   `calculate_loyalty_discount(loyalty_points, tier, order_total, product_category="standard") -> str`
   decorated with `@tool`.
2. The tool SHALL build a self-contained Python code string that defines
   `earn_rates = {"standard": 1, "device": 2, "fresh": 5}` and
   `tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}`.
3. The code SHALL compute `points_redeemed` floored to the nearest 500 and capped so the points
   dollar value does not exceed 50% of the order total; compute `tier_discount` on the subtotal
   after points; and compute `final_total`, `total_savings`, `points_earned`, and
   `remaining_points`. It SHALL print a JSON result dict.
4. The tool SHALL execute the code via `code_session(REGION).invoke("executeCode", {...})` with
   `language="python"` and `clearContext=True`, and return the first result event as a JSON string.
5. IF the Code Interpreter is unavailable THEN the tool SHALL fall back to a tier-discount-only
   computation and return a JSON string (never raise).
6. The returned structured result SHALL contain all required fields: `points_redeemed`,
   `tier_discount` (and/or rate), `final_total`, `total_savings`, `points_earned`, and
   `remaining_points`. (Rubric 5: "structured result containing all required fields".)

### Requirement 8 — Agent entrypoint (TODO 8)

**User Story:** As a client, I want a single entrypoint that assembles all tools, memory, and
browser for each request so that one call handles any supported customer workflow.

#### Acceptance Criteria

1. The system SHALL define `invoke(payload, context=None)` decorated with `@app.entrypoint`.
2. WHEN invoked THEN the system SHALL extract `prompt` (required), `customer_id` (actor id), and
   `session_id` from `payload`, generating a UUID WHEN `session_id` is missing.
3. The system SHALL instantiate a `MemoryHook` for this actor/session and an
   `AgentCoreBrowser(region=REGION)`.
4. The system SHALL build a base tools list
   `[search_knowledge_base, calculate_loyalty_discount, <browser tool>]`, connect to the Gateway
   via `MCPClient(lambda: streamable_http_client(GATEWAY_URL))`, load the gateway tools inside the
   client context, and extend the tools list with them.
5. WHILE inside the MCP client context THEN the system SHALL create the `Agent` (model, tools,
   hooks=[memory_hook], a clear `system_prompt`) and invoke it with the user input, and SHALL
   return the text of the first content block of the response.
6. IF the prompt is missing OR any step raises THEN the entrypoint SHALL return a readable error
   string rather than raising.

### Requirement 9 — Deploy, permissions, and functional verification

**User Story:** As the submitter, I need the agent deployed and all six scenarios passing so that I
can collect evidence for the rubric.

#### Acceptance Criteria

1. The agent SHALL be configurable and deployable with
   `agentcore configure --entrypoint main.py --name <name> --deployment-type direct_code_deploy
   --runtime PYTHON_3_13 --disable-memory` followed by `agentcore deploy`.
2. AFTER deploy, running `python setup_permissions.py` (in the activated venv) SHALL succeed,
   granting the runtime role `bedrock:Retrieve` on the KB, memory read/write, and browser-session
   permissions.
3. The deployed agent SHALL satisfy the six tests:
   - **T1 Order tracking** (ORD-001): returns SHIPPED status, TRK987654321, UPS, an estimated
     delivery date.
   - **T2 Refund** (ORD-002 Kindle Paperwhite): returns a refund ID, APPROVED, "3-5 business days".
   - **T3 RAG** (Platinum tier benefits): returns free same-day shipping, 15% discount, priority
     support, sourced from the KB.
   - **T4 Memory** (two sessions, same `customer_id`): session B recalls "Jane" and the preference
     for concise responses after memory extraction.
   - **T5 Discount** (Gold, 4250 pts, $150 standard): returns points redeemed, 10% tier discount,
     correct final total, remaining points.
   - **T6 Browser** (fetch a live page title): given the prompt URL `https://www.udacity.com`,
     returns the page title from that live site. (The source checklist's "Amazon.com" note is a
     copy-paste error; the prompt URL is Udacity and the title must match the URL actually given.)

### Requirement 10 — Deliverable hygiene

**User Story:** As a grader, I need a clean submission so that it meets the rubric.

#### Acceptance Criteria

1. `main.py` SHALL contain no remaining `pass`/`None` TODO placeholders and no leftover
   `# TODO` scaffolding lines marked "Replace this line".
2. The repository SHALL include captured terminal output (or screenshots) for all six tests.
3. The repository SHALL include a 200–400 word reflection covering: one design decision, one
   challenge and its resolution, and one production-hardening consideration.
4. The reflection SHALL name a specific tool or integration from the project when explaining the
   design decision, describe a concrete challenge and how it was resolved, and discuss at least one
   production consideration (scalability, reliability, security, observability, or cost).
   (Rubric 7.)

### Requirement 11 — Stand-out enhancements (optional, implement if low-risk)

**User Story:** As a reviewer, I want the submission to demonstrate integration-readiness beyond
the baseline so that it stands out.

#### Acceptance Criteria (each optional; include only if they do not jeopardize the six tests)

1. The system MAY define Pydantic models for key tool responses (e.g. discount breakdown, order
   status) and validate tool output before returning it.
2. The system MAY summarize older conversation turns when history exceeds a token budget, instead
   of dropping them.
3. These enhancements SHALL NOT change the entrypoint signature, the six test behaviors, or the
   rubric-required function shapes. IF an enhancement risks a baseline criterion THEN it SHALL be
   omitted.

> Decision for this build: baseline (R1–R10) is implemented and verified first. Stand-out item
> 11.1 (Pydantic validation of the discount breakdown) is the lowest-risk addition and MAY be
> layered on after the six tests pass. Items 11.2 and domain personalization are out of scope for
> this submission to avoid risking the rubric tests.

### Requirement 12 — Engineering standards (cross-cutting, state-of-the-art bar)

**User Story:** As a maintainer, I want the project to follow known good practices so that it is
readable, resilient, idempotent, observable, and lint-clean — not merely rubric-passing.

#### Acceptance Criteria

1. Every `@tool` and the entrypoint SHALL wrap their body in try/except catching a specific/`Exception`
   type, log the failure via the module logger, and return a readable string — never crash the
   runtime. There SHALL be no bare `except: pass`.
2. Capabilities SHALL degrade gracefully and independently (KB unconfigured, Code Interpreter down,
   memory error, gateway unreachable) without failing unrelated capabilities.
3. All provisioning/sync tooling SHALL be **idempotent**: re-running creates no duplicates
   (describe-or-create by deterministic name; update-in-place). The agent SHALL be safe to re-invoke
   for read-only tools; the only state-changing tool (`initiate_refund`) SHALL be called only on
   explicit user intent.
4. The code SHALL emit observability logs: an INFO line per tool call (tool name + non-sensitive
   args) and WARNING/ERROR on failures with context; the entrypoint SHALL log `actor_id`/`session_id`
   (identifiers, not PII values).
5. The code SHALL pass **Ruff** lint + format with zero errors (config in `ruff.toml`); public
   functions SHALL have type hints and docstrings; `python -m py_compile` SHALL succeed. These gate
   every wave.
6. Network operations SHALL set timeouts where the SDK permits (gateway connect, browser session).
7. No secrets SHALL appear in code, config, logs, or the committed repo; least-privilege IAM via the
   provided `setup_permissions.py` policy SHALL be used unchanged.
8. Deployed dependencies SHALL be pinned to the versions verified locally where an API is relied on
   (e.g. `strands-agents-tools>=0.2.21,<0.9.0`), because `direct_code_deploy` re-resolves from
   `pyproject.toml`; and module import SHALL stay cheap and crash-proof (lazy client/model creation,
   heavy/optional/version-sensitive imports performed inside the functions that use them) so the
   runtime always initializes within its init window.

### Requirement 13 — Externalized configuration (no magic values)

**User Story:** As an operator, I want all tunable values in one external file so that I can change
behavior without editing code and without duplicated constants.

#### Acceptance Criteria

1. A `config.yaml` beside `main.py` SHALL be the source of truth for: `model_id`, `region`,
   `gateway_url`, `knowledge_base_id`, `memory_id`, retrieval `top_k` and chunk separator, loyalty
   `earn_rates`/`tier_rates`/`points_per_dollar`/`min_redemption`/`max_redeem_fraction`, browser
   `session_timeout`, memory `retrieve_top_k`, gateway connect timeout, and the system-prompt path.
2. A `load_config()` function SHALL read `config.yaml` (PyYAML) merged over built-in defaults, and
   SHALL return safe defaults WHEN the file is missing or invalid, so startup never fails.
3. BECAUSE `setup_permissions.py` AST-reads literals, `REGION`, `KB_ID`, and `MEMORY_ID` SHALL remain
   literal string assignments in `main.py`, kept in sync with `config.yaml` by an idempotent
   `sync_config.py`. All other values SHALL be read only from config (no duplicated literals).
4. The system prompt SHALL live in an external `system_prompt.txt` referenced by config, not inline
   in `main.py`.
5. Loyalty rates used by both the Code Interpreter path and the fallback SHALL come from the same
   config source (no divergent copies).

### Requirement 14 — Documentation (kept in lock-step with the build)

**User Story:** As a developer or grader, I want accurate, current documentation so that I can set
up, run, deploy, and understand the project without reading all the code.

#### Acceptance Criteria

1. The repository SHALL maintain a three-doc set, each with a distinct audience:
   - **Root `README.md`** — the upstream course brief, kept **untouched** except for a short pointer
     banner at the top linking to `starter/README.md` and `DEPLOYMENT.md`.
   - **`starter/README.md`** — the developer guide for THIS implementation: venv setup, the
     `config.yaml` + `sync_config.py` model, how to run Ruff, the wave structure, deploy steps, and
     where the spec lives. Replaces the stub.
   - **`DEPLOYMENT.md`** (root) — the operational/build log: environment facts, SDK versions and the
     SDK-surface verification, config architecture, infra provisioning commands/click-paths and the
     resulting resource IDs, deploy, the six test captures, and teardown.
2. `config.yaml` SHALL be self-documenting (inline comments on each key).
3. Documentation SHALL be updated as part of **every wave hand-off**: `DEPLOYMENT.md` records what was
   actually built/verified in that wave, and `starter/README.md` is updated whenever behavior or the
   workflow changes. Docs SHALL NOT describe unbuilt or unverified state as done.
4. Docs SHALL NOT contain secrets or real credentials; resource IDs in `DEPLOYMENT.md` are
   non-secret identifiers (account ID MAY be masked per submission rules).
5. The repository SHALL include **`docs/ARCHITECTURE.md`** with rendered (Mermaid) diagrams and
   accompanying prose: (a) system architecture, (b) single-invocation request flow, (c) tool-routing
   decision flow, (d) cross-session memory sequence (the T4 flow), (e) deployment/provisioning
   order (all-code `infra.provision`/`infra.destroy` + `agentcore` Runtime), and (f) configuration flow
   (`config.yaml` → `load_config()`/`sync_config.py` → `main.py` literals → `setup_permissions.py`).
   Diagrams SHALL be Mermaid code blocks (text, GitHub-renderable, no binary assets) and SHALL be
   kept consistent with the implementation.

### Requirement 15 — Infrastructure as code (no console; fully reversible)

**User Story:** As an operator, I want to deploy and destroy ALL AWS resources via code with no UI
steps, so that the environment is reproducible, idempotent, and cleanly reversible.

#### Acceptance Criteria

1. ALL AWS resources SHALL be provisioned by code using the venv boto3 and `.env` credentials: IAM
   roles, the two Lambdas, the REST API + stage, S3 + catalog upload, OpenSearch Serverless
   collection + policies + index, the managed Knowledge Base + data source + ingestion, the
   AgentCore Gateway + two targets, and AgentCore Memory + two strategies. No console/UI step SHALL
   be required.
2. The agent Runtime SHALL be deployed and torn down via the toolkit CLI (`agentcore
   configure`/`deploy`/`destroy`) — code/CLI, not UI.
3. Provisioning SHALL be **idempotent** (describe-or-create by deterministic name; update-in-place;
   re-run is a no-op) and SHALL use waiters (not fixed sleeps) for async states (Memory ACTIVE,
   ingestion COMPLETE, collection ACTIVE, IAM propagation).
4. A single entry point (`python -m infra.provision`) SHALL create everything in dependency order;
   a single entry point (`python -m infra.destroy`) SHALL delete everything in reverse order,
   idempotently (safe if partially created or already deleted).
5. On success, provisioning SHALL write `GATEWAY_URL`, `KB_ID`, `MEMORY_ID` into `config.yaml` and
   run `sync_config.py` so `main.py` literals are set without manual editing.
6. Verification of the provisioned stack SHALL also be code (identity check, programmatic MCP tool
   listing, a KB `retrieve` probe, Memory `get_memory` ACTIVE check) — no UI.
7. The NONE-authorizer Gateway SHALL be created only for this sandbox and SHALL be removed by
   `infra.destroy`.
