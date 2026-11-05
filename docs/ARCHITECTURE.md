# Architecture — Customer Support AI Agent

Rendered diagrams (Mermaid) and prose for the Amazon Bedrock AgentCore + Strands customer support
agent. Diagrams are text (diff-friendly, no binaries) and render natively on GitHub. They are kept
consistent with the implementation; see `../.kiro/specs/customer-support-agent/design.md` for the
authoritative design and `../DEPLOYMENT.md` for the build log.

---

## (a) System architecture

The deployed agent runs in the AgentCore Runtime and reaches five integrations. Order/refund tools
arrive over MCP from the Gateway; the other capabilities are local tools or AWS calls.

```mermaid
flowchart LR
    client["Client<br/>agentcore invoke"] --> rt["AgentCore Runtime<br/>BedrockAgentCoreApp (ASGI)"]
    rt --> agent["Strands Agent<br/>Amazon Nova 2 Lite"]

    subgraph tools["Agent tools"]
        kb["search_knowledge_base"]
        calc["calculate_loyalty_discount"]
        br["AgentCore Browser"]
        gw["Gateway MCP tools<br/>(orders, refunds)"]
    end
    agent --> kb
    agent --> calc
    agent --> br
    agent --> gw

    agent -. hooks .-> mem["AgentCore Memory<br/>facts + preferences"]

    kb --> kbapi["Bedrock KB Retrieve API"] --> oss[("OpenSearch Serverless")]
    s3[("S3: product_catalog.txt")] -. ingested .-> oss
    calc --> ci["Code Interpreter sandbox"]
    gw --> gwsvc["AgentCore Gateway<br/>(NONE auth, MCP)"]
    gwsvc --> apigw["API Gateway REST (prod)"] --> lo["Lambda: order-tracker"]
    gwsvc --> lr["Lambda: refund-processor"]
    agent --> model["Bedrock: global.amazon.nova-2-lite-v1:0"]
```

All of the above (except the account-level Code Interpreter/Browser and the model) is provisioned
as code by `starter/infra/`; the Runtime is deployed by the AgentCore toolkit.

---

## (b) Single-invocation request flow

Each `invoke` builds a per-request agent inside the MCP client context, lets memory enrich the
prompt, runs the model/tools, then persists the turn.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant E as invoke() entrypoint
    participant H as MemoryHook
    participant M as AgentCore Memory
    participant G as MCP Gateway
    participant A as Agent (Nova Lite)
    participant T as Selected tool

    C->>E: {prompt, customer_id, session_id?}
    E->>E: validate prompt; session_id = uuid if absent; log identifiers
    E->>H: MemoryHook(actor, session)
    E->>G: open MCPClient context, list_tools_sync()
    E->>A: Agent(model, [kb, calc, browser, *gateway_tools], hooks=[H], system_prompt)
    A->>H: MessageAddedEvent (user message)
    H->>M: retrieve_memories(facts, preferences, top_k)
    M-->>H: relevant memories
    H-->>A: prepend "Customer Context: ..." to the user message
    A->>T: tool call (if the model decides to)
    T-->>A: tool result
    A-->>E: assistant response
    A->>H: AfterInvocationEvent
    H->>M: create_event([(query, USER), (response, ASSISTANT)])
    E-->>C: first content-block text
```

---

## (c) Tool-routing decision flow

The model routes each prompt to the right capability based on the system prompt and tool
docstrings. Simplified decision view:

```mermaid
flowchart TD
    start(["Customer prompt"]) --> q{"Intent?"}
    q -->|"Track order / refund / return label"| gw["Gateway MCP tool<br/>get_order / initiate_refund / ..."]
    q -->|"Product spec, policy, warranty,<br/>loyalty rules, recommendations"| kb["search_knowledge_base (RAG)"]
    q -->|"Discount / final price / points math"| calc["calculate_loyalty_discount<br/>(Code Interpreter)"]
    q -->|"Open a live URL / real-time web info"| br["AgentCore Browser"]
    q -->|"General chat / clarification"| direct["Answer directly<br/>(grounded, concise)"]
    gw --> ans(["Summarize result for the customer"])
    kb --> ans
    calc --> ans
    br --> ans
    direct --> ans
```

---

## (d) Cross-session memory sequence (Test 4)

Two separate sessions, same `customer_id`. Session A writes an interaction; AgentCore extracts
facts/preferences asynchronously; Session B retrieves them.

```mermaid
sequenceDiagram
    autonumber
    participant A as Session s-A
    participant Ag as Agent + MemoryHook
    participant M as AgentCore Memory
    participant X as Async extraction
    participant B as Session s-B

    A->>Ag: "Hi, I am Jane. I prefer concise responses." (CUST-123)
    Ag->>M: create_event([(user, Jane/concise), (assistant, ...)])
    M->>X: extract into namespaces
    X-->>M: cs_agent/CUST-123/facts = {name: Jane}
    X-->>M: cs_agent/CUST-123/preferences = {concise}
    Note over X,M: ~30s extraction latency
    B->>Ag: "Do you remember my name and preference?" (CUST-123)
    Ag->>M: retrieve_memories(facts, preferences)
    M-->>Ag: Jane + concise
    Ag-->>B: "Yes, Jane — I'll keep it concise."
```

---

## (e) Deployment / provisioning order (all code)

Resources are created in dependency order by `infra.provision`, then the Runtime via the toolkit;
teardown is the reverse via `agentcore destroy` + `infra.destroy`.

```mermaid
flowchart TD
    subgraph prov["python -m infra.provision (idempotent)"]
        i1["1. IAM roles"] --> i2["2. Lambdas<br/>order-tracker, refund-processor"]
        i2 --> i3["3. REST API + stage prod"]
        i3 --> i4["4. S3 + product_catalog.txt"]
        i4 --> i5["5. OpenSearch Serverless<br/>collection + policies + index"]
        i5 --> i6["6. Knowledge Base + data source + ingestion → KB_ID"]
        i6 --> i7["7. Gateway (NONE) + 2 targets → GATEWAY_URL"]
        i7 --> i8["8. Memory + 2 strategies → MEMORY_ID"]
        i8 --> i9["outputs.py → config.yaml → sync_config.py → main.py literals"]
    end
    i9 --> d1["agentcore configure"]
    d1 --> d2["agentcore deploy (Docker)"]
    d2 --> d3["setup_permissions.py"]
    d3 --> d4["infra.verify (identity, MCP list, KB probe, Memory ACTIVE)"]
    d4 --> tests["Six functional tests"]
    tests --> td["Teardown: agentcore destroy → infra.destroy (reverse)"]
```

---

## (f) Configuration flow

`config.yaml` is authoritative. Most values are read directly; three are mirrored into `main.py`
literals because `setup_permissions.py` AST-reads them.

```mermaid
flowchart LR
    yaml["config.yaml<br/>(single source of truth)"]
    yaml -->|load_config()| mainpy["main.py runtime values<br/>model_id, rates, prompt path,<br/>GATEWAY_URL, retrieval, timeouts"]
    yaml -->|sync_config.py| lits["main.py literals<br/>REGION, KB_ID, MEMORY_ID"]
    lits -->|AST literal read| perms["setup_permissions.py<br/>→ runtime-role IAM policy"]
    infra["infra/outputs.py"] -->|writes IDs| yaml
    txt["system_prompt.txt"] -->|system_prompt_path| mainpy
```

The idempotent `sync_config.py` only rewrites the three literal lines; `infra.outputs` closes the
loop by writing provisioned IDs back into `config.yaml` so nothing is hand-edited.
