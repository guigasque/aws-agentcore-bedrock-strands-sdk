# MCP Gateway — tool-invocation evidence (resubmission)

This log addresses the review note on *"Integrate external tools using MCP"*: it shows each
Gateway-backed tool invocation returning a well-formed response, plus the error-handling path when
the Gateway is unreachable.

- **Runtime:** `cs_support_agent-yMZc9TGr2D` (us-east-1, acct 024333017526)
- **Gateway:** `CustomerSupportGateway` → `…/mcp` (status READY, 6 tools)
- **Targets:** `order-tracker` (API Gateway REST) and `refund-processor` (Lambda)

All identifiers below are non-secret (session/request/account IDs). No credentials are shown.

---

## 1. Well-formed responses, end-to-end

Run against the deployed agent with `agentcore invoke`.

### T1 — order lookup through the Gateway

Prompt: `What is the status of order ORD-001?`

```
The status of order ORD-001 is SHIPPED.
- Items: Wireless Headphones Pro (1 x $89.99)
- Total: $89.99
- Tracking Number: TRK987654321
- Carrier: UPS
- Estimated Delivery: November 9, 2026
```

### T2 — refund through the Gateway

Prompt: `I need a refund for order ORD-002, it arrived damaged.`

```
Refund ID: REF-GSEA702P
Amount: $139.99 (full refund for the Kindle Paperwhite)
Status: Approved
Timeline: credit in 3-5 business days
```

---

## 2. Runtime log — tools load and invoke cleanly

CloudWatch runtime log for the test session. The `Gateway connected; loaded 6 tool(s).` line is
the new `logger.info` on the success path; each `Tool #N` line is a well-formed tool call.

```
INFO:CSAI_Agent:invoke actor_id=CUST-123 session_id=final-t2
INFO:CSAI_Agent:Gateway connected; loaded 6 tool(s).
Tool #1: order-tracker___get_order
Tool #2: refund-processor___initiate_refund
INFO:CSAI_Agent:Saved interaction to memory for actor_id=CUST-123

INFO:CSAI_Agent:invoke actor_id=CUST-123 session_id=final-t3
INFO:CSAI_Agent:Gateway connected; loaded 6 tool(s).
Tool #1: search_knowledge_base

INFO:CSAI_Agent:invoke actor_id=CUST-123 session_id=final-t5
INFO:CSAI_Agent:Gateway connected; loaded 6 tool(s).
Tool #1: calculate_loyalty_discount
```

---

## 3. Error handling when the Gateway is unreachable

The `invoke()` entrypoint wraps the MCP client in nested try/except: an outer handler for failing
to open the client, an inner handler around `list_tools_sync()` that catches `TimeoutError`,
`ConnectionError`, and any other `Exception` with `logger.exception(...)`. On any failure the agent
falls back to the local tools and still answers, rather than returning an internal error.

Verified locally by pointing `GATEWAY_URL` at a non-existent host:

```
ERROR:CSAI_Agent:Gateway unavailable (the client initialization failed ...); using local tools only.
```

The agent still called `search_knowledge_base` and returned a complete answer — the failing
integration degraded independently instead of taking down the request. No secret values are logged;
failures are referenced by message only.

---

## Reproduce

```bash
cd starter && source .venv/bin/activate && set -a; source ../.env; set +a
agentcore invoke '{"prompt": "What is the status of order ORD-001?", "customer_id": "CUST-123", "session_id": "t1"}'
agentcore invoke '{"prompt": "I need a refund for order ORD-002, it arrived damaged.", "customer_id": "CUST-123", "session_id": "t2"}'

# view the Gateway tool-load + invocation lines in the runtime log:
aws logs tail /aws/bedrock-agentcore/runtimes/cs_support_agent-yMZc9TGr2D-DEFAULT \
  --log-stream-name-prefix "$(date -u +%Y/%m/%d)/[runtime-logs" --since 10m \
  | grep -E "Gateway connected|Tool #"
```
