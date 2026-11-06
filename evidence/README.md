# Evidence — Functional Test Outputs

Screenshots of the six required test scenarios (plus the deploy), captured against the deployed
agent `cs_support_agent`
(ARN `arn:aws:bedrock-agentcore:us-east-1:024333017526:runtime/cs_support_agent-yMZc9TGr2D`).
All six tests passed.

| Screenshot (`img/`) | Test | Capability | Result shown |
|---|---|---|---|
| `T0 — Deploy.png` | — | `agentcore deploy` | Deployment completed successfully (bonus) |
| `T1 — Order tracking.png` | T1 | Order tracking (MCP gateway) | ORD-001 SHIPPED, TRK987654321, UPS, ETA Nov 8 2026 |
| `T2 — Refund.png` | T2 | Refund (MCP gateway) | Refund ID REF-…, $139.99, Approved, 3-5 business days |
| `T3 — RAG.png` | T3 | Knowledge Base (RAG) | free same-day shipping, 15% discount, priority support |
| `T4 — Memory .png` | T4 | Cross-session memory | session e4-B recalls "Jane" + concise preference |
| `T5 — Loyalty discount.png` | T5 | Loyalty discount (Code Interpreter) | 10% tier ($11), 4000 pts, final $99.00, 349 remaining |
| `T6 — Browser.png` | T6 | Browser tool | live Udacity title: "Learn the Latest Tech Skills; Advance Your Career | Udacity" |

The written reflection is `reflection.md`. See `../DEPLOYMENT.md` for the full build log and
`../docs/HOW_IT_WORKS.md` for how each capability works.

## How these were produced

```bash
cd starter && source .venv/bin/activate && set -a; source ../.env; set +a
agentcore invoke '{"prompt": "...", "customer_id": "CUST-123", "session_id": "..."}'
```

Exact prompts (per the project brief):

- **T1** `Can you track order ORD-001?`
- **T2** `I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.`
- **T3** `What are the benefits of the Platinum loyalty tier?`
- **T4** session A `Hi, I am Jane. I prefer concise responses.` → (wait for async extraction) →
  session B `Do you remember my name and communication preference?`
- **T5** `I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.`
- **T6** `Go to https://www.udacity.com and tell me the page title.`

Note (T4): cross-session memory extraction is asynchronous / eventually-consistent (~60-90s), so the
recall session must run after extraction completes. The screenshots use non-secret identifiers only
(session/request IDs, account ID); no credentials are shown.
