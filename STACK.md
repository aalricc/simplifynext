# Stack mapping

Every item on the hackathon kick-off slide, where it lives in this repo, and
**where a judge can see it during the 3-minute demo**. If it is not visible on
screen, it does not count.

| Kick-off item | Where it lives | Visible in the demo as |
|---|---|---|
| **MCP** | MCP tool server (8085); called by Finder, CDR and Pipeline | "MCP tool calls" panel, bottom left — `search`, `places`, `rag_retrieve`, `persist_and_schedule`, `inbox` with their arguments |
| **AG-UI** | `ui_client/agui/` (CopilotKit) + `POST /ag-ui` on 8084 | The artifact drawer. Agent tool calls mount real components — research brief, content package, critique card, email, calendar — not chat text |
| **OpenTelemetry** | Spans named `{agent}.{pattern}` on every agent | The live agent trace. Each row is a span: agent name, pattern badge, service, summary |
| **LangGraph** | P2's graphs in the CDR service | Sequential and loop badges — `ResearchPlannerAgent`, the `HookCriticAgent` → `RewriteAgent` loop |
| **DeepAgents** | `CDRRootAgent`, the root planner — DeepAgents-*style* (plan → delegate to subgraphs as tools), hand-written on LangGraph, not the `deepagents` package | First line of every run: "DeepAgents root … delegating to finder, research, package, outreach subgraphs" |
| **Claude Agent SDK** | Optional specialist critic | `FactCheckCriticAgent` (pattern `tool`) |
| **AWS Bedrock AgentCore** | Deploy target for the recorded demo | Not on screen — deployment target, called out verbally |
| **Groq** | Local inference for the `llm`-pattern agents | Green `llm` badges: `HookWriterAgent`, `ScriptWriterAgent`, `EmailDraftAgent`, `ReplyClassifierAgent` |

## Agent patterns on screen

The trace badges the pattern for every agent, because "which pattern is this"
is the question judges actually ask.

| Badge | Meaning | Example in the demo |
|---|---|---|
| `parallel` | Fan-out / gather | `FinderFanoutAgent` starts 4 scouts at 0:52 |
| `sequential` | Ordered pipeline | `ResearchPlannerAgent` → `ResearchSynthAgent` |
| `loop` | Iterative refinement, max 3 | `HookCriticAgent` fails → `RewriteAgent` → passes |
| `tool` | Agent-as-tool / MCP call | `LocalPlacesAgent`, `RAGRetrieverAgent` |
| `custom` | Hand-written control flow | `OpportunityDedupeAgent`, `SendGateAgent` |
| `llm` | Single model call | `HookWriterAgent`, `CaptionAgent` |

Across the two runs, **39 distinct named agents** appear on screen: 13 parallel,
12 llm, 7 tool, 6 sequential, 6 loop, 5 custom steps.

## Human-in-the-loop

Deliberately minimal. One **Run campaign** click is the only required human action
in the entire demo. `PAUSE_BEFORE_SEND` exists as a toggle and defaults **off** —
outreach sends without approval. There is a Stop button; it ends the run, it does
not gate it.

## Protocol note

The UI never learns whether events came from a fixture file or from P2's live
agent — both arrive as the same AG-UI SSE stream from `POST /ag-ui`. Going live is
`USE_FIXTURES=0`, not a rewrite. See [`demo/fixtures/README.md`](demo/fixtures/README.md)
for the event contract and [`cdr/agui_map.py`](cdr/agui_map.py) for the one
module that holds P2's schemas to it.

If the live CDR stops answering mid-run, the board says so on the trace and
keeps the events it already drew; if it never answered at all, the run falls
back to the fixture replay and labels itself. Either way the demo does not hang.
