# Taking this to production

The agent as built is tuned for a graded sandbox: clear code, correct behavior, everything rebuilt
per request. That's the right call here. This note is the other conversation — what I'd change if
real traffic hit it, and roughly in what order. It's grounded in what the code actually does today,
not generic advice.

## Where the time goes right now

Every `invoke` does this, in series, before the model even starts thinking:

1. Build the MemoryHook — which calls `get_memory_strategies` (one round-trip) on every request.
2. `retrieve_customer_context` — two **sequential** `retrieve_memories` calls (facts, then preferences).
3. Open the MCP client and `list_tools_sync()` — a handshake plus tool-list fetch to the Gateway,
   every request, even when the question never needs a gateway tool.
4. Instantiate the browser tool. (The actual browser *session* only cold-starts if used — and that's
   the slowest thing in the system, seconds not milliseconds.)

Then the model runs, and after the reply is composed, `save_support_interaction` does one more
round-trip to write the turn.

So a simple "what's your return policy?" can pay for ~4 network round-trips that have nothing to do
with answering it. That's the first thing I'd attack.

## Latency — biggest wins first

**Cache what doesn't change per request.**
- `get_namespaces()` is static for the life of the memory resource. Fetch it once, keep it
  module-level, stop paying for it on every call.
- The Gateway tool list barely changes. Cache it (short TTL) or hold a warm MCP connection instead
  of re-handshaking each time.
- Those two together take ~2–3 round-trips out of the hot path.

**Run the two memory reads at the same time.** Facts and preferences are independent lookups done
one after the other today. `asyncio.gather` (or a small thread pool) cuts that wait roughly in half.

**Don't touch the Gateway unless you need it.** Today we open the MCP connection on every request.
Most questions never call an order/refund tool. Open it lazily on first gateway-tool use, or reuse a
pooled connection across invocations.

**Write to memory in the background.** The `create_event` at the end still delays the response.
Return the answer first, persist the turn asynchronously.

**Trim the prompt.** The memory preamble and retrieved KB chunks are input tokens on every turn —
they cost both latency and money. Measure whether `top_k=3` answers as well as `top_k=5`, and cap how
much memory context gets prepended.

| Change | Effort | Payoff |
|---|---|---|
| Cache namespaces + gateway tool list | low | −2–3 round-trips per request |
| Parallelize the two memory reads | low | ~half the memory-retrieval wait |
| Background the memory write | low–med | drops a round-trip off response time |
| Lazy / pooled gateway connection | med | skip the handshake when it isn't used |
| Smaller `top_k`, trimmed context | med | lower latency and token cost |

## Scaling

The agent itself is stateless per request — all the state lives in AgentCore Memory — so it scales
sideways without much drama. The limits you actually hit are downstream: Bedrock model TPS quotas,
Knowledge Base Retrieve throughput, Lambda concurrency, Gateway limits. Going to production means
requesting those quota bumps up front and adding retries with backoff + jitter so a burst doesn't
turn into a thundering-herd failure.

Cold starts are real too — the deploy bundle is ~100 MB and the imports are heavy. We already defer
the browser/MCP/code-interpreter imports and build clients lazily. Beyond that: slim the
dependencies and keep a warm instance if the traffic pattern justifies it.

## Cost

Tokens dominate. The memory context and KB chunks we prepend are paid input on every turn, so
trimming them is a cost lever as much as a latency one. Nova Lite is already the cheap/fast tier,
which helps. Two more:

- **Cache common KB answers.** "What's the return policy?" shouldn't hit Retrieve every time.
- **Summarize old turns** instead of carrying the full history once a conversation gets long (the
  course's "conversation summarization" suggestion — it's a cost control, not just a context trick).

One cost decision is already banked: using **S3 Vectors** for the Knowledge Base instead of
OpenSearch Serverless means no always-on capacity billing when the agent is idle.

## Reliability and operating it

- **Timeouts and retries** on every downstream call. Today failures degrade to a readable message,
  which is fine, but there's no retry — a single blip becomes a visible failure.
- **Circuit breakers** per tool, so a flapping Gateway doesn't add its timeout to every request.
- **Metrics worth watching:** per-tool latency and error rate, tokens per turn, and
  memory-recall hit rate (is the memory actually helping, or just adding tokens?). Observability
  (CloudWatch Logs + X-Ray) is already on; these are custom metrics to add on top.

## Security (the non-negotiable before real traffic)

The Gateway uses the **NONE authorizer** — fine for a throwaway sandbox, not for anything real.
Before production it has to move to SigV4 or JWT, which also means the agent signs its MCP calls.
IAM is already scoped per-resource (no wildcard admin), and no secrets live in the repo; those stay.

## Architecture: microservices? Kubernetes? (probably not)

A common instinct at scale is "break it into microservices and run it on K8s." For this system that
would mostly add complexity without solving a real problem — and it helps to see why.

**It's already decomposed where it matters.** The agent is one deployable unit, but the work is
spread across services that scale on their own: the order/refund tools are independent **Lambdas**
behind a managed **MCP Gateway**; RAG is Bedrock KB + S3 Vectors; memory, code interpreter and
browser are managed AgentCore services. The Lambdas behind the Gateway *are* microservices — we just
didn't have to run any of it.

**Splitting the agent itself buys nothing here.** The test for a service boundary is a genuine
difference in scaling, deployment cadence, or team ownership. The agent's capabilities share one
request/response path and scale together per conversation. Pulling "the KB tool" or "the memory hook"
into their own services would add network hops (latency up), more things to deploy/secure/monitor,
and distributed-failure handling — to buy nothing. That's premature decomposition. The one defensible
split later is a **slow/async tool** (the browser, with its seconds-long sessions) behind its own
service so it doesn't tie up the agent — but that's an optimization for a measured problem, not a
starting shape.

**Kubernetes would be a step backwards.** AgentCore already gives a managed, autoscaling runtime with
observability and no nodes to own. Moving to EKS means taking back node pools, patching, scaling
policies and ingress to replace something AWS runs for us. K8s earns its keep only if we outgrow a
hard AgentCore limitation we can *name* (a latency profile, a sidecar pattern, a networking need it
can't express) — this workload doesn't.

**The structural changes that would actually matter, in order:**
1. **Event-driven for slow/side-effecting work.** Make refunds and ingestion fire-and-forget to a
   queue (SQS/EventBridge); the agent returns "in progress." This decouples user-facing latency from
   backend work and is the highest-value change for a real product.
2. **Multi-agent (orchestrator → workers)** — a supervisor routing to specialized sub-agents (orders,
   returns). This is the agentic equivalent of microservices, and it's worth it only when a single
   agent's tool list/prompt grows large enough that routing degrades. Five capabilities fit one agent
   comfortably; revisit when that stops being true.
3. **Shared tool layer** — if other agents/apps reuse the order/refund tools, the Gateway already is
   that shared layer. Already built that way.

Short version: keep the current shape; if real traffic demands it, go **event-driven for the slow
paths** and consider an orchestrator/sub-agent split — not containers on K8s.

## What I would NOT do yet

Not speculatively optimize. The right first step is to add the latency metrics above, deploy, watch
where time actually goes, and fix the proven hot spot. Most of the wins listed here are cheap, but a
few (warm connections, caching) trade simplicity for speed and introduce staleness/lifecycle
questions — worth it under load, not worth it on day one.
