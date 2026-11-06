# Reflection

Building this customer support agent on Amazon Bedrock AgentCore brought together five distinct
integrations behind a single Strands agent, and the most interesting lessons came from the seams
between them.

**A design decision.** For the Knowledge Base vector store I chose **S3 Vectors** instead of the
OpenSearch Serverless backing the course implies. The RAG tool (`search_knowledge_base`) only needs
the Bedrock Retrieve API, so the store behind it is an implementation detail — and S3 Vectors is far
simpler to provision as code (a vector bucket plus an index, versus an OpenSearch collection with
three separate security policies and a hand-built index) and, critically, has no always-on cost. The
course's own cleanup notes warn that idle OpenSearch Serverless capacity units get expensive; S3
Vectors removes that risk entirely. The trade-off is that it is newer and less battle-tested, but for
a grounded product catalog it worked on the first ingestion and returned the exact expected chunks.

**A challenge.** My first cloud deployment crashed on startup with a misleading "runtime
initialization timeout." The real cause, found in CloudWatch logs, was an `ImportError`: the deployed
runtime resolved a newer `strands-agents-tools` (0.9.0) than my local environment, and 0.9.0 had
removed the `AgentCoreBrowser` class I imported at module top level. I fixed it two ways: pinning the
dependency (`<0.9.0`) so the deployed image matches what I tested, and refactoring to lazy imports
and cached client construction so no optional, version-sensitive dependency is touched at import
time. Module import now always completes within the initialization window, and a broken optional tool
degrades gracefully instead of taking down the whole agent.

**A production consideration.** Reliability depends on treating every integration as fallible. Each
tool and the entrypoint catch specific exceptions, log, and return a readable message rather than
raising — so an unconfigured Knowledge Base, an unavailable Code Interpreter, or a slow Gateway never
crashes a conversation. For real production I would add least-privilege, per-resource IAM (the
sandbox NONE-authorizer Gateway must be replaced with signed auth), structured metrics and alarms on
tool error rates and latency, and explicit handling of AgentCore Memory's eventual consistency, which
surprised me during testing when recall briefly lagged extraction.
