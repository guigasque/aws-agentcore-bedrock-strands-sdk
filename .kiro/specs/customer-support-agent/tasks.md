# Implementation Plan — Customer Support AI Agent (Wave-based)

Implementation proceeds in **waves**. Each wave:
- is a single, self-contained unit of work on `starter/main.py` (+ supporting files);
- leaves the module **importable and deployable** (`app` exists, `@app.entrypoint` exists, parses);
- is **independently runnable/testable** (at least a basic invoke; capability waves add one test);
- ends with the agent handed back for **your review + your commit** (the agent does not run git).

Mapping notation: each task cites the TODO it fills, the requirement(s) it satisfies, and the
official rubric criterion. Environment: venv at `starter/.venv` (pip, not `uv`); AWS creds from
`.env` (`set -a; source ../.env; set +a`).

**Standards gate (every wave):** before hand-off, `python -m py_compile main.py` and
`ruff check` MUST be clean; new public functions have type hints + docstrings; tools/entrypoint
have specific-exception try/except with logging; values come from `config.yaml` (no magic
numbers); **docs updated** (`DEPLOYMENT.md` records what the wave built/verified; `starter/README.md`
updated if workflow/behavior changed). See Requirements 12, 13, 14.

Resource dependency note: a capability's **code wave** can be committed before its AWS resource
exists (the tool degrades gracefully — "not configured"/graceful error). Its **test** runs once the
matching resource is provisioned (Wave I). Code waves and provisioning interleave.

Suggested commit messages are shown per wave; you adapt/commit as you prefer.

**Checkbox legend:** `[x]` done & verified · `[~]` partially done · `[ ]` not started.

**Progress (updated each wave):** Wave 0 ✅ · Wave 1 ✅ · Wave 2 ✅ (T3) · Wave 3 ✅ (T5) ·
Wave 4 ✅ (T6) — all cloud-verified · Wave I: KB layer ✅ + `agent_permissions`
(Code Interpreter + Browser + KB Retrieve) ✅ · Next: Wave 5 (MCP gateway — orders & refunds).

---

## Wave 0 — Spec, env, tooling scaffold, docs (no `main.py` logic change)
_Commit: `chore: spec, config scaffold, ruff, docs, gitignore`_

- [x] 0.1 Spec committed under `.kiro/specs/customer-support-agent/` (this plan + requirements + design).
- [x] 0.2 `.gitignore` excludes `.env`, `.venv/`, `build/`, `*.egg-info/`.
- [x] 0.3 `DEPLOYMENT.md` (root) — operational/build log: env facts, pinned SDK versions, SDK-surface
  verification, config architecture; infra/deploy/test/teardown sections stubbed for later waves.
- [x] 0.4 `starter/config.yaml` — self-documenting source of truth (model_id, region, gateway_url,
  kb/memory ids, retrieval, loyalty rates, browser, timeouts, system_prompt_path).
- [x] 0.5 `starter/system_prompt.txt` — externalized system prompt.
- [x] 0.6 `starter/ruff.toml` — lint + format config; install `ruff` into the venv (dev tool).
- [x] 0.7 `starter/sync_config.py` — idempotent: rewrites only the `REGION`/`KB_ID`/`MEMORY_ID`
  literals in `main.py` from `config.yaml`. Re-run is a no-op.
- [x] 0.8 `starter/README.md` — rewrite the stub into the developer guide (venv, config model, ruff,
  wave structure, deploy, spec pointer).
- [x] 0.9 Root `README.md` — add a short pointer banner at the top to `starter/README.md` and
  `DEPLOYMENT.md`; leave the rest of the brief untouched.
- [x] 0.10 `docs/ARCHITECTURE.md` — Mermaid diagrams + prose: (a) system architecture,
  (b) single-invoke request flow, (c) tool-routing decision flow, (c2) KB data source & ingestion,
  (d) cross-session memory sequence (T4), (e) deployment/provisioning order, (f) configuration flow.
  Plus `docs/HOW_IT_WORKS.md` (narrative overview). Cross-linked from both READMEs.
  - _Requirements: 12.3, 12.5, 13.1-13.5, 14.1-14.6; documentation_

---

## Wave 1 — Runnable skeleton (deployable agent, config loader, no tools/memory yet)
_Commit: `feat(todo1,3,8): runnable Nova Lite agent skeleton + config loader`_
_Fills: TODO 1, TODO 3, minimal TODO 8, `load_config()`. TODO 2 = literals mirrored from config._

- [x] 1.1 `load_config()` — read `config.yaml` over minimal defaults; safe fallback on missing/invalid.
  - _Requirements: 13.2_
- [x] 1.2 TODO 1 — `app = BedrockAgentCoreApp()`; keep `BYPASS_TOOL_CONSENT="true"`; `__main__` → `app.run()`.
  - _Requirements: 1.1, 1.2, 1.3 — Rubric 1_
- [x] 1.3 TODO 2 — `REGION`/`KB_ID`/`MEMORY_ID` as literals (synced from config via `sync_config.py`);
  `GATEWAY_URL = _CFG.get("gateway_url", ...)`. Shape-valid placeholders until Wave 7/I.
  - _Requirements: 2.1, 2.2, 2.3, 13.3_
- [x] 1.4 TODO 3 — lazy `get_model()` / `get_memory_client()` / `get_bedrock_runtime()` (cached);
  `model_id` from config. (Refactored to lazy init after the Wave 1 cold-start incident.)
  - _Requirements: 3.1, 3.2, 3.3, 13.1, 12.8_
- [x] 1.5 TODO 8 (minimal) — `@app.entrypoint async def invoke(...)`: validate `prompt`; extract
  `customer_id`/`session_id` (uuid fallback); log identifiers; build `Agent(get_model(), tools=[],
  system_prompt=<loaded>)`; invoke; `_response_text()`; specific-exception try/except.
  - _Requirements: 8.1, 8.2, 8.5, 8.6, 12.1, 12.4 — Rubric 1_
- [x] 1.6 Gate + verify: ruff + py_compile clean; local smoke OK; **deployed to cloud** (ARN
  `cs_support_agent-yMZc9TGr2D`) and `agentcore invoke` returned a coherent response. Fixed cold-start
  import crash via lazy imports + `strands-agents-tools<0.9.0` pin + `.dockerignore`.
  - _Deployable + runnable checkpoint met._

---

## Wave 2 — Knowledge Base RAG tool
_Commit: `feat(todo6): knowledge base search tool (RAG)`_
_Fills: TODO 6; adds the tool to the entrypoint tools list._

- [x] 2.1 TODO 6 — `@tool search_knowledge_base(query)`: `_is_configured(KB_ID)` guard →
  `"Knowledge base not configured."`; `get_bedrock_runtime().retrieve(...)` with config-driven
  `top_k` (via `retrievalConfiguration`); empty → no-results msg; else join with configured
  separator; specific-exception → readable error; INFO log; docstring states when to use it.
  - _Requirements: 6.1–6.5 — Rubric 3_
- [x] 2.2 Add `search_knowledge_base` to the entrypoint tools list.
  - _Requirements: 8.4_
- [x] 2.3 Verify: ruff + compile clean; KB provisioned (Wave I.5–7); **T3 PASSED** — "Platinum
  loyalty tier benefits" → free same-day shipping, 15% discount, priority support (grounded).
  - _Test: T3 — DONE._

---

## Wave 3 — Loyalty discount via Code Interpreter
_Commit: `feat(todo7): loyalty discount calculator (code interpreter)`_
_Fills: TODO 7; adds the tool to the entrypoint tools list._

- [x] 3.1 TODO 7 — build the self-contained f-string code (`earn_rates`, `tier_rates`,
  points-floor-to-500 + 50%-cap, tier discount, totals, earned/remaining, `print(json.dumps(...))`).
  Rates injected from `config.yaml`. **DONE.**
  - _Requirements: 7.2, 7.3_
- [x] 3.2 Execute via `code_session(REGION).invoke("executeCode", {"language":"python",
  "clearContext":True,"code":code})`; return first `event["result"]` as JSON string. Lazy import. **DONE.**
  - _Requirements: 7.1, 7.4_
- [x] 3.3 Tier-only fallback on failure (never raise); all required fields present
  (`points_redeemed`, `tier_discount`(+rate), `final_total`, `total_savings`, `points_earned`,
  `remaining_points`). **DONE (fallback exercised when local role lacked Code Interpreter).**
  - _Requirements: 7.5, 7.6 — Rubric 5_
- [x] 3.4 Add tool to entrypoint; ruff+compile; **deployed + T5 PASSED in cloud** — Gold/4250/$150
  standard → final `99.00`, 10% tier, 4000 redeemed, 349 remaining (live sandbox, not fallback).
  Required granting Code Interpreter to the runtime role via `infra/agent_permissions.py`.
  - _Test: T5 — DONE._

---

## Wave 4 — Browser tool
_Commit: `feat(todo8): agentcore browser tool`_
_Fills: browser portion of TODO 8._

> **Caveat (from Wave 1):** `strands_tools.browser.AgentCoreBrowser` was removed in
> `strands-agents-tools` 0.9.0. We are pinned `<0.9.0`, so the starter approach works. This wave
> decides: (a) stay pinned and use `AgentCoreBrowser` via the lazy `_browser_tools()` helper, or
> (b) migrate to `bedrock_agentcore.tools.browser_client.BrowserClient` + Playwright in a custom
> `@tool`. Default: (a) for now; revisit if the pin becomes a problem.

- [x] 4.1 Implement `_browser_tools()`: lazily import `AgentCoreBrowser`, instantiate with
  `region=REGION` and `session_timeout` from config, return `[browser.browser]`; on ImportError log
  and return `[]` (graceful degradation). Entrypoint calls it. **DONE.**
  - _Requirements: 8.3, 8.4, 12.1, 12.2 — Rubric 6_
- [x] 4.2 ruff + compile; **deployed + T6 PASSED in cloud** — `https://www.udacity.com` → page title
  *"Learn the Latest Tech Skills; Advance Your Career | Udacity"*. Browser IAM granted via
  `infra/agent_permissions.py`.
  - _Test: T6 — DONE._

---

## Wave 5 — MCP Gateway tools (orders + refunds)
_Commit: `feat(todo8): connect MCP gateway tools`_
_Fills: MCP portion of TODO 8._

- [ ] 5.1 Open `MCPClient(lambda: streamable_http_client(GATEWAY_URL))`; inside the context,
  `tools.extend(mcp_client.list_tools_sync())`; create + run the `Agent` inside the context.
  - _Requirements: 8.4, 8.5 — Rubric 2_
- [ ] 5.2 Verify compile + deploy; (once Gateway exists) T1 — track ORD-001 → SHIPPED,
  TRK987654321, UPS, ETA; T2 — refund ORD-002 Kindle → refund ID, APPROVED, "3-5 business days".
  - _Test: T1 + T2 (≥2 MCP tool invocations, well-formed) (needs GATEWAY_URL)._

---

## Wave 6 — Cross-session memory hook
_Commit: `feat(todo4,5): cross-session memory hook`_
_Fills: TODO 4 + TODO 5; wires `hooks=[memory_hook]` into the Agent._

- [ ] 6.1 TODO 4 — `get_namespaces(mem_client, memory_id)`: `get_memory_strategies`;
  `namespaceTemplates[0]` with `namespaces[0]` fallback; handle `type`/`memoryStrategyType`;
  return `{strategy_type: template}`.
  - _Requirements: 4.1, 4.2, 4.3 — Rubric 4_
- [ ] 6.2 TODO 5.1 — `MemoryHook.__init__` stores 4 attrs + `self.namespaces = get_namespaces(...)`.
  - _Requirements: 5.1_
- [ ] 6.3 TODO 5 — text-extraction helper (plain-text user vs tool-result/tool-use).
  - _Requirements: 5.2_
- [ ] 6.4 TODO 5 — `retrieve_customer_context`: guard; per-namespace `retrieve_memories(...,top_k=5)`;
  tag by strategy; prepend `"Customer Context:\n...\n\n"`; try/except logs + continues.
  - _Requirements: 5.2, 5.3, 5.6_
- [ ] 6.5 TODO 5 — `save_support_interaction`: walk backwards for last user + assistant;
  `create_event(..., messages=[(q,"USER"),(a,"ASSISTANT")])`; try/except logs + continues.
  - _Requirements: 5.4, 5.6_
- [ ] 6.6 TODO 5 — `register_hooks`: `MessageAddedEvent`→retrieve, `AfterInvocationEvent`→save.
  - _Requirements: 5.5_
- [ ] 6.7 Wire `hooks=[memory_hook]` into the Agent; verify compile + deploy; T4 — session s-A
  ("Hi, I am Jane. I prefer concise responses."), wait ~30s, session s-B recall → "Jane" + concise.
  - _Test: T4 (needs MEMORY_ID)._

---

## Wave 7 — Final config + system prompt polish
_Commit: `feat(todo2): real resource IDs + finalized system prompt`_

- [ ] 7.1 TODO 2 — real `GATEWAY_URL`/`KB_ID`/`MEMORY_ID` are written by `infra.outputs` into
  `config.yaml` and mirrored into `main.py` by `sync_config.py` (no manual editing). Verify they
  are present and shape-valid.
  - _Requirements: 2.1-2.4, 13.3, 15.5_
- [ ] 7.2 Finalize `SYSTEM_PROMPT` (role, all tools, usage rules: KB for product/policy/loyalty-program
  & product recommendations; calculator for discount math; order tools with `customer_id`; concise).
  - _Requirements: 8.5_
- [ ] 7.3 Static sweep: no `= None  # Replace this line`, no bare TODO `pass`, no leftover `<...>`;
  `ruff check`; `python -m py_compile main.py`; `python setup_permissions.py --dry-run`.
  - _Requirements: 10.1, 2.4, 12.5_

---

## Wave I — Infrastructure as code (`infra/` package; interleaved; unblocks the tests)
_Commit(s): `feat(infra): idempotent boto3 provisioner + destroyer`_
_All-code, no console. Can land its code before the agent waves need the IDs; sub-layers may be
committed incrementally. Each module is idempotent (describe-or-create) and has a matching destroy._

- [x] I.1 `infra/common.py` — boto3 session from `.env`, tagging, logging, describe-or-create and
  waiter helpers; `infra/state.json` read/write for created IDs/ARNs. **DONE (Wave 2).**
  - _Requirements: 15.1, 15.3, 12.3_
- [~] I.2 `infra/iam.py` — KB service role (least privilege) **DONE (Wave 2)**; Lambda execution role
  pending (Wave 5).
  - _Requirements: 15.1, 12.7_
- [ ] I.3 `infra/lambdas.py` — zip + create/update `order-tracker`, `refund-processor` (Py 3.12).
  - _Requirements: 15.1_
- [ ] I.4 `infra/apigw.py` — REST API + 3 resources + GET Lambda-proxy methods
  (`get_order`/`get_customer_orders`/`get_customer`) + deploy stage `prod`.
  - _Requirements: 15.1_
- [x] I.5 `infra/s3_catalog.py` — bucket + upload `product_catalog.txt`. **DONE (Wave 2).**
  - _Requirements: 15.1_
- [x] I.6 `infra/s3vectors.py` — **S3 Vectors** bucket + index (1024-dim cosine, Titan v2), replacing
  OpenSearch Serverless (simpler + cheaper). **DONE (Wave 2).**
  - _Requirements: 15.1, 15.3_
- [x] I.7 `infra/knowledge_base.py` — managed KB (S3_VECTORS) + data source + `StartIngestionJob` +
  wait COMPLETE → `KB_ID`. **DONE (Wave 2): KB_ID=YPXPQVRBJX.**
  - _Requirements: 15.1, 15.3_
- [ ] I.8 `infra/gateway.py` — Gateway (NONE auth) + target `order_tracker` (API GW, 3 ops) +
  `refund_processor` (Lambda, schema `lambda/lambda_schema`) → `GATEWAY_URL`.
  - _Requirements: 15.1, 15.7_
- [ ] I.9 `infra/memory.py` — Memory + strategies `customer_facts`→`cs_agent/{actorId}/facts`,
  `customer_preferences`→`cs_agent/{actorId}/preferences`; wait ACTIVE → `MEMORY_ID`.
  - _Requirements: 15.1, 15.3_
- [x] I.10 `infra/outputs.py` — write `GATEWAY_URL`/`KB_ID`/`MEMORY_ID` into `config.yaml`; run
  `sync_config.py` to mirror the three literals into `main.py`. **DONE (Wave 2): KB_ID synced.**
  - _Requirements: 15.5, 13.3_
- [~] I.11 `infra/provision.py` (`python -m infra.provision`) — run all layers in order; then
  outputs. Idempotent. **KB-only subset `infra/provision_kb.py` DONE (Wave 2);** full provision pending.
  - _Requirements: 15.3, 15.4_
- [ ] I.12 `infra/destroy.py` (`python -m infra.destroy`) — delete in reverse order, idempotent and
  safe on partial/already-deleted state.
  - _Requirements: 15.4, 15.7_
- [ ] I.13 `infra/verify.py` — code-only checks: identity, programmatic MCP tool listing, KB
  `retrieve` probe ("return policy for electronics" → 15-day), Memory `get_memory` ACTIVE.
  - _Requirements: 15.6_
- [ ] I.14 Record all commands + resulting IDs in `DEPLOYMENT.md` (no secrets).
  - _Requirements: 14.1, 14.3_

---

## Wave 8 — Deploy, test capture, reflection
_Commit: `test: six scenario outputs + reflection`_

- [ ] 8.1 Provision + deploy (all code): `python -m infra.provision`; `agentcore configure ...
  --deployment-type direct_code_deploy --runtime PYTHON_3_13 --disable-memory`; `agentcore deploy`;
  `python setup_permissions.py`; `python -m infra.verify`.
  - _Requirements: 9.1, 9.2, 15.1-15.6 — Rubric 1_
- [ ] 8.2 Run + capture all six tests under `evidence/` (T1–T6); MCP log shows ≥2 tool calls.
  - _Requirements: 9.3, 10.2 — Rubric 1–6_
- [ ] 8.3 Reflection `evidence/reflection.md` (200–400 words): named tool + design rationale;
  concrete challenge + resolution; ≥1 production consideration.
  - _Requirements: 10.3, 10.4 — Rubric 7_
- [ ] 8.4 Docs: finalize `DEPLOYMENT.md` (provision + deploy output, all resource IDs, six test
  captures, teardown) and `starter/README.md` (final run/test instructions).
  - _Requirements: 14.1, 14.3_
- [ ] 8.5 Teardown rehearsal (optional, if lab time allows): `agentcore destroy` + `python -m
  infra.destroy` leaves no residual resources; re-running is a no-op.
  - _Requirements: 15.4, 15.7_

---

## Wave 9 — Optional polish + packaging
_Commit(s): `feat: pydantic discount validation` (optional), `chore: package submission.zip`_

- [ ] 9.1 (Optional) Pydantic `DiscountBreakdown` validation in TODO 7; omit if it risks any test.
  - _Requirements: 11.1, 11.3_
- [ ] 9.2 Package `submission.zip`: completed `main.py`, `evidence/` (6 outputs + reflection),
  unchanged support files, `DEPLOYMENT.md`, spec. Exclude `.env`, `.venv/`, `build/`, `*.egg-info/`.
  Verify the archive listing.
  - _Requirements: 10.1, 10.2, 10.3_

---

## Wave dependency summary

| Wave | Deployable | Test it unlocks | Needs resource |
|------|-----------|-----------------|----------------|
| 1 | ✅ | basic invoke | none |
| 2 | ✅ | T3 (RAG) | KB_ID |
| 3 | ✅ | T5 (discount) | none (account Code Interpreter) |
| 4 | ✅ | T6 (browser) | none (account browser + IAM) |
| 5 | ✅ | T1, T2 (gateway) | GATEWAY_URL |
| 6 | ✅ | T4 (memory) | MEMORY_ID |
| 7 | ✅ | all six | all IDs |
