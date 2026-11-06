# Reflection

This agent ties five AgentCore integrations together behind one Strands agent; the sharpest lessons
came from the seams between them.**A design decision.** For the Knowledge Base vector store I chose **S3 Vectors** instead of the
OpenSearch Serverless the course implies. The RAG tool only needs the Bedrock Retrieve API, so the
store behind it is an implementation detail — and S3 Vectors is far simpler to provision as code (a
vector bucket plus an index, versus an OpenSearch collection with three security policies and a
hand-built index) and has no always-on cost. The course's own cleanup notes warn that idle
OpenSearch capacity units get expensive; S3 Vectors removes that risk. It's newer and less
battle-tested, but for a grounded product catalog it worked on the first ingestion.

**A challenge.** My first cloud deployment crashed on startup with a misleading "runtime
initialization timeout." The real cause, in the CloudWatch logs, was an `ImportError`: the deployed
runtime resolved a newer `strands-agents-tools` (0.9.0) than my laptop had, and 0.9.0 deleted the
`AgentCoreBrowser` class I imported at the top of the file. I fixed it two ways — pinned the
dependency (`<0.9.0`) so the deployed image matches what I tested, and moved the version-sensitive
imports (browser, MCP, code interpreter) inside the functions that use them, with clients built on
first use. Now the module always finishes importing in time, and one broken optional tool can't take
the whole agent down.

**A production consideration.** The thing I'd fix first isn't reliability — it's that every request
pays for work it doesn't need. Before answering a simple "what's your return policy?", the code
already makes several blocking calls: it fetches the memory strategies, runs two sequential memory
lookups, and opens an MCP connection to list gateway tools the question will never use. For the
sandbox that's fine and keeps the code readable. Under real traffic I'd cache the static bits (the
namespace list, the gateway tool list), run the two memory reads in parallel, and only touch the
Gateway when an order/refund tool is actually needed — removing three or four round-trips from the
hot path. I'd add timeouts and retries on every downstream call (today one blip becomes a visible
failure) and per-tool latency metrics, so I optimize the proven hot spot instead of guessing. And
the NONE-authorizer Gateway, fine only in a throwaway sandbox, would move to signed auth. More detail
in `docs/PRODUCTION.md`.
