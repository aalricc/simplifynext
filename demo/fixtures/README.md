# Fixture event streams

These files are the **contract P4 renders against**. Until P2's `/ag-ui` is live,
the UI server replays them; on day 5 the same components consume P2's real stream.
If P2 emits these event shapes, the UI needs no changes.

| File | Story |
|---|---|
| `run_events.jsonl` | Week 1 — parallel research, package, critique fail → rewrite, schedule, outreach |
| `week2_events.jsonl` | Week 2 — brand replied, analytics land, memory adapts the plan |

One JSON object per line. `_delay_ms` is the pause **before** that event and is
stripped before it goes on the wire.

## Wire events (real AG-UI)

Everything the browser receives is standard AG-UI over SSE:

```
RUN_STARTED / RUN_FINISHED / RUN_ERROR      always carry runId + threadId
TEXT_MESSAGE_START / _CONTENT / _END        assistant narration
TOOL_CALL_START / _ARGS / _END              generative UI - the cards
CUSTOM                                      board panels (see below)
```

## Authoring conveniences

Two shapes exist only in the fixture files so they stay hand-editable. `expand()`
in [`ui_client/server.py`](../../ui_client/server.py) turns them into the real
protocol events above.

```jsonc
// becomes TOOL_CALL_START + TOOL_CALL_ARGS(delta=JSON string) + TOOL_CALL_END
{"type":"TOOL_CALL","toolCallId":"tc_1","toolCallName":"render_qa_verdict","args":{…}}

// becomes TEXT_MESSAGE_START + TEXT_MESSAGE_CONTENT + TEXT_MESSAGE_END
{"type":"TEXT_MESSAGE","messageId":"m1","content":"…"}
```

## CUSTOM events → board panels

| `name` | `value` | Panel |
|---|---|---|
| `agent_trace` | `{agent, pattern, service, status, summary}` | Live agent trace ticker |
| `mcp_call` | `{server, tool, args_summary}` | MCP tool calls |
| `opportunities` | `{opportunities: [{opportunity_id, type, title, score, status, rationale}]}` | Opportunity table |
| `pipeline` | `{updates: [{opportunity_id, status}]}` | Kanban |
| `engagement` | `{messages: [{id, from, channel, received, preview, classification}]}` | Engagement inbox |
| `memory` | `{entries: [{id, week, insight, source, confidence, changed_from?}]}` | What we learned |

`pattern` must be one of `parallel | sequential | loop | tool | custom | llm`.
`status` is `running | done | fail` and drives the row colour — `fail` is what
makes the critique loop visible.

### Where the live agent meets this contract

P2 emits these same events. The translation from CDR's own schemas lives in
[`cdr/agui_map.py`](../../cdr/agui_map.py) — one module, so P2 keeps its
schemas and P4 keeps its components. Two vocabularies deliberately differ and
are mapped there rather than changed at either end:

| Board / fixtures | `shared.schemas` | Why |
|---|---|---|
| `qualified` | `OpportunityStatus.researched` | Kanban columns predate the enum; the fixtures speak the board's vocabulary |
| `outreach_sent` | `outreached` | |
| `replied` | `engaged` | |
| `negotiating` | `meeting` | |
| score `0.0-1.0` | Finder score `0-100` | The board formats two decimals |

Changing either vocabulary means changing `PIPELINE_STATUS` in `agui_map.py`,
not the fixtures and not the components.

## TOOL_CALL names → components

Each name maps to one component in
[`ui_client/static/components.js`](../../ui_client/static/components.js) and
[`ui_client/agui/src/components/cards.tsx`](../../ui_client/agui/src/components/cards.tsx).

```
render_research_brief     render_content_package    render_qa_verdict
render_outreach_email     render_dm_script          render_call_script
render_calendar_week      render_engagement_reply   render_analytics
render_plan_adaptation
```

**A tool name with no registered component still renders** — as a card showing its
raw args — so a new P2 tool appears on screen the day it ships rather than
silently disappearing.

## Editing

Keep every line valid JSON. Check before committing:

```bash
python3 -c "import json,sys;[json.loads(l) for l in open(sys.argv[1]) if l.strip()];print('ok')" demo/fixtures/run_events.jsonl
```
