# CreatorLoop

Agentic studio manager for content creators. It **plans** a week of work, **acts** on the pipeline (research, package, critique, outreach), and **adapts** the next run from what came back.

Built for the SimplifyNext Agentic AI Hackathon 2026. Kick-off problem: a solution that **plans, acts, and adapts over time**; we chose creators going from irregular posting to a repeatable studio. The human **supervises the agent** — one Run click. Named agents do the grind.

Software AI track. The official kick-off stack — **MCP, AWS Bedrock AgentCore, AG-UI, OpenTelemetry, LangGraph / DeepAgents / Claude Agent SDK**, plus Groq — is mapped to where it appears on screen in [`STACK.md`](STACK.md).

## Who it serves

Anyone who signs up. A creator makes an account, answers a short onboarding form, and the agents plan their week against **their** niche, city, voice and constraints.

### Demo personas

Three creators ship as demo accounts. Each is an ordinary user — no service special-cases them — and each tells a different story on the board:

| Persona | Sign in as | Niche | The brand deal | The critique catches |
|---|---|---|---|---|
| **Maya Tan** `@mayacooks.sg` | `maya@creatorloop.local` | Hawker-style home cooking | Laksa Lab paste | An unsourced calorie claim |
| **Henry Lim** `@henrypulls` | `henry@creatorloop.local` | Pokémon openings and grading | Orchard Card Bar | An invented pull rate |
| **John Tan** `@johnthrifts` | `john@creatorloop.local` | Thrift flipping and resale | Loop Vintage | A made-up resale valuation |

Password for all three: `creatorloop-demo`.

```bash
python scripts/seed_demo_user.py           # all three
python scripts/seed_demo_user.py henry     # just one
python scripts/seed_demo_user.py --list
```

Each runs the same arc — plan a week, fail the critique, rewrite, send outreach, then adapt from week-2 replies — with its own opportunities, voice, brand targets and failure mode. Switching persona on camera is a sign-out and a sign-in.

**Adding a fourth is adding a directory.** Copy `demo/henry/` to `demo/<name>/`, edit the JSON, re-run the seed script. [`shared/fixtures.py`](shared/fixtures.py) documents the file layout and the small rules format that lets an agent branch on its prompt — which is how the fail-then-fix beat works. `pytest tests/test_personas.py` checks a new persona has every file, defines every agent the graph calls, selects opportunity ids that exist, and actually fails then passes its critique.

## Run it

You need a database and one API key. Both are free.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Put a free [Groq](https://console.groq.com) key in `.env` as `GROQ_API_KEY`, then bring up Postgres and the six services:

```bash
docker compose up            # Postgres + migrations + all services
```

No Docker? Everything runs on SQLite instead — same models, same migrations:

```bash
export DATABASE_URL="sqlite+aiosqlite:///./creatorloop.db"
./scripts/run_local.sh       # Windows: .\scripts\run_local.ps1
```

Then open [http://localhost:8000](http://localhost:8000), create an account, and finish onboarding. To skip straight to a populated board:

```bash
python scripts/seed_demo_user.py
# maya@ / henry@ / john@creatorloop.local — password creatorloop-demo
```

### What costs money

Nothing in the default setup.

| Thing | Cost | Notes |
|---|---|---|
| Postgres | free | Local container, or SQLite with no container at all |
| **Groq** (`LLM_PROVIDER=groq`) | **free tier** | The default. A campaign is 60–80 calls and fits comfortably |
| AWS Bedrock (`LLM_PROVIDER=bedrock`) | paid | Optional. Billed to the hackathon sandbox lease — see below |
| Tavily / Google Places / SMTP | paid or free tier | **Optional.** Unset, the MCP tools use seeded local data |

`DAILY_RUN_CAP` (default 20 runs per profile per day) is the guard against one account emptying a shared key.

### Looking inside the database

```bash
python scripts/db_peek.py                   # row counts, accounts, profiles
python scripts/db_peek.py --profile maya    # everything for one creator
python scripts/db_peek.py opportunities     # dump one table
```

Works against whatever `DATABASE_URL` points at, so the same command answers
"is my data actually there" on SQLite or Postgres. On SQLite the whole database
is the single file named in `DATABASE_URL` (default `./creatorloop.db`), which
any SQLite GUI will open; on Postgres, `psql creatorloop`.

### Tests

```bash
pytest -q
```

Runs on SQLite, no services needed. [`tests/test_tenancy.py`](tests/test_tenancy.py) is the important one — it proves one creator cannot read another's rows.

### The keyless fixture demo

[`ui_client/server.py`](ui_client/server.py) still replays [`demo/fixtures/*.jsonl`](demo/fixtures/) with **no database, no keys and no pip install**. It cannot sign anyone in and does not read the database — it exists so the recorded demo always has a fallback.

```bash
python3 ui_client/server.py          # board only, fixture replay
DEMO_SPEED=0.6 python3 ui_client/server.py   # the cue sheet's pacing
```

### Full stack (UAT)

`./scripts/run_local.sh` (or `.\scripts\run_local.ps1`) applies migrations, then brings up Finder / Pipeline / Engagement / CDR / MCP / UI together. With services up, run the frozen-HTTP smoke suite:

```bash
python scripts/uat_smoke.py     # Windows: .\scripts\uat_smoke.ps1
```

22 checks across all six services. It signs up a throwaway account, completes onboarding, runs a full campaign and drives the week-2 adapt loop — so it exercises the thing most likely to break, identity surviving all five hops. Exit code is the number of failures.

Set `USE_FIXTURES=1` for a keyless UAT run: the agents return canned output, exercising the whole graph and event stream without calling a model.

### The AG-UI client (CopilotKit)

The static board above is the no-keys fallback. The AG-UI client is the real one:

```bash
cd ui_client/agui && npm install && npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Same board, plus a CopilotKit
sidebar — ask the CDR agent for something and the tool calls it makes render as
the same components inline. It connects with `@ag-ui/client`'s `HttpAgent` and
registers every card on `CopilotKitProvider`'s `renderToolCalls`.

`package.json` pins `@ag-ui/client` to `0.0.57` under `overrides` on purpose:
CopilotKit 1.69 depends on that exact version, and a second copy in the tree
makes `HttpAgent` fail to typecheck as an `AbstractAgent`. Bump both together.

### `USE_FIXTURES`

Defaults to **`0`**. With it on, every named agent returns canned Maya output from
[`shared/fixtures.py`](shared/fixtures.py) — correct for tests, wrong for anyone
who is not Maya, which is why it is no longer the default and no longer a
silent fallback.

| Process | `USE_FIXTURES=0` (default) | `USE_FIXTURES=1` |
|---|---|---|
| `ui_client/app.py` | Proxies the live CDR at `CDR_AGUI_URL` | (same — this flag does not affect it) |
| `ui_client/server.py` | Proxies the live CDR | Replays `demo/fixtures/*.jsonl` |
| `cdr`, `opportunity_finder`, `mcp` | Real inference (needs `GROQ_API_KEY`) | Canned output — real graph, no model calls |

A full three-opportunity live campaign takes roughly 3–5 minutes on Groq,
against about half a second on canned output.

**Missing credentials now fail loudly.** Previously an unset key silently fell
back to Maya's canned answers, so a creator in Lisbon would be shown Singapore
laksa opportunities and told they were results. The run reports the failure on
the trace instead.

### Running on AWS Bedrock

`LLM_PROVIDER` picks the backend for every named agent. No agent code changes.

```bash
LLM_PROVIDER=groq      # default: fast, free, what the demo runs on
LLM_PROVIDER=bedrock   # Claude on Bedrock, billed to the sandbox lease
```

To use Bedrock, fill these in `.env` (they ship as `PASTE_..._HERE`
placeholders, which the code treats as unset):

```bash
LLM_PROVIDER=bedrock
AWS_REGION=us-east-1              # Bedrock's region, NOT the portal's ap-southeast-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_SESSION_TOKEN=...             # required - sandbox credentials are temporary
BEDROCK_MODEL_ID=anthropic.claude-haiku-4-5
```

Get all three values from **AWS access portal → Accounts → expand your account
→ Access keys → Option 1**. They **expire every 12 hours**, so re-copy them
before a session.

Check what is actually running — this reports the effective configuration, not
what you asked for:

```bash
curl -s localhost:8084/health
# {"runtime":"bedrock","provider":"bedrock","model_id":"anthropic.claude-haiku-4-5",
#  "region":"us-east-1","credentials":"ok", ... }
```

`runtime` is `fixtures` whenever `USE_FIXTURES=1` **or** credentials are
missing, because that is what the agents will really do.

> **Budget.** The lease is capped at **US$20** (access revoked there, account
> terminated at $30) and there is one lease per team, no second chances. A full
> campaign is 60-80 model calls — cents on Haiku. What actually burns the cap is
> always-on infrastructure, so keep running the stack locally and use Bedrock
> for inference only. Watch the budget bar in the Innovation Sandbox portal; it
> lags by a few hours.
>
> **AgentCore Memory is not implemented.** `harness/agentcore.py`'s `put_memory`
> is a no-op. Memory is the `memory` table in Postgres, one row per creator. Do
> not describe AgentCore Memory as working.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | Postgres on localhost | `sqlite+aiosqlite:///./creatorloop.db` also works |
| `INTERNAL_SIGNING_KEY` | dev key | Signs the tenant header. **Set this in production** |
| `CREATORLOOP_ENV` | `dev` | `production` forces Secure cookies and a real signing key |
| `USE_FIXTURES` | `0` | `1` returns canned agent output (tests) |
| `DAILY_RUN_CAP` | `20` | Campaign runs per profile per day. `0` disables |
| `SESSION_DAYS` | `30` | How long a sign-in lasts |
| `CORS_ORIGINS` | `UI_URL` + Vite | Browser origins allowed to call the services |
| `CDR_AGUI_URL` | `http://localhost:8084/ag-ui` | Live AG-UI endpoint |
| `LIVE_TIMEOUT` | `300` | Seconds to wait on the live stream |
| `PAUSE_BEFORE_SEND` | `0` | Optional HITL toggle. Off by design |

## Architecture

Five application services plus MCP, over one Postgres database:

| Service | Port | Owner | Job |
|---|---|---|---|
| UI Client (AG-UI / CopilotKit) | 8000 | P4 | **Accounts, onboarding**, campaign board, generative UI. |
| Opportunity Finder | 8081 | P1 | Discover trends, brand-gaps, collabs. |
| Pipeline Manager | 8082 | P3 | Persist, qualify, calendar, memory, RAG corpus. |
| Engagement Listener | 8083 | P3 | Inbound replies + analytics → status + memory. |
| CDR Agent + AG-UI | 8084 | P2 | LangGraph graphs; DeepAgents root; `POST /ag-ui`. |
| MCP tool server | 8085 | P1+P3 | Search, places, persist, calendar, inbox, RAG retrieve. |

CDR = Content Development Representative.

```
browser ── session cookie ──> UI :8000        the only public service
                                 │
                                 │ signed X-CreatorLoop-Profile header
                                 ▼
                              CDR :8084 ──> MCP :8085 ──> Pipeline :8082
                                                              │
Engagement :8083 ──────────────────────────────────────────> Postgres
OTEL spans on every named agent
```

### Tenancy — read this before adding a service

**`creator_profiles` is the tenancy unit, not `users`.** One user can own
several profiles, so every row that belongs to a creator hangs off
`profile_id`. Schema lives in [`shared/models.py`](shared/models.py); if a
table is not there, it does not exist.

Identity travels as a **signed header**, not as a function argument — see
[`shared/tenant.py`](shared/tenant.py). It rides in a `ContextVar`, the same
way `cdr/runtime.py` already carries `run_id`, so adding a tool or an agent
does not mean threading tenancy through its signature.

Three rules that keep this honest:

1. **Only `ui_client` publishes a port.** The backend services have no
   authentication of their own; they trust the header ui_client mints after
   checking the session cookie. Publishing their ports would bypass login
   entirely.
2. **Build outbound clients with `shared.http_clients.client()`.** A raw
   `httpx.AsyncClient` sends no tenant headers, and the receiving service will
   reject it with `401 no_tenant`.
3. **Never default a missing profile.** `require_profile()` raises. A demo
   persona substituted for a missing tenant is a confident wrong answer, which
   is worse than a visible failure.

`pytest tests/test_tenancy.py` is the guard on all of this.

About **34 distinct named agents** (llm, sequential, parallel, loop, tool, custom). Do not collapse them into one mega-prompt. The list per service lives in each service's `agents/` folder; **39** appear by name across the two demo runs.

### Patterns that must show up in the demo

- Parallel fan-out/gather on research
- Review/critique with structured `{verdict, issues, must_fix}`
- Iterative refinement loop (max 3), including a visible fail-then-fix on Maya
- Agent-as-tool (parent agent calls subgraphs / other services as tools)
- Human-in-the-loop is **minimal**: one Run click. Optional `PAUSE_BEFORE_SEND` defaults off
- **AG-UI dynamic rendering**: artifacts show up as UI components, not only chat text
- **OTEL** spans named by agent + pattern

## What the board shows

| Panel | Source |
|---|---|
| Campaign bar | niche, city, profile = Maya, **Run campaign** — the only required human action |
| Live agent trace | `CUSTOM/agent_trace` — agent name, pattern badge, service, summary. OTEL span names, on screen |
| MCP tool calls | `CUSTOM/mcp_call` — which MCP tool ran with what arguments |
| Opportunity table | `CUSTOM/opportunities` — type, title, score, status |
| Pipeline kanban | `CUSTOM/pipeline` — P3's `OpportunityStatus` values |
| Calendar strip | `render_calendar_week` tool call |
| Engagement inbox | `CUSTOM/engagement` — week-2 inbound with classification |
| Memory panel | `CUSTOM/memory` — "what we learned", with `was:` showing what a week-2 entry replaced |
| Artifact drawer | `TOOL_CALL_*` — research brief, content package, critique fail → rewrite, email, DM, call script, analytics, plan adaptation |

The full event contract, including how to add a new render tool, is in [`demo/fixtures/README.md`](demo/fixtures/README.md).

## Team

Work on your own branch, merge to `main` every night. P1 is merge captain for `shared/`.

| Person | Branch | Prompt |
|---|---|---|
| P1 Opportunity Finder + platform | `p1-finder` | `prompts/P1_opportunity_finder.md` |
| P2 CDR orchestrator | `p2-cdr` | `prompts/P2_cdr.md` |
| P3 Pipeline + engagement | `p3-pipeline` | `prompts/P3_pipeline_engagement.md` |
| P4 UI + demo story | `p4-ui` | `prompts/P4_ui_demo.md` |

**Rule:** if a field is not in `shared/schemas.py`, it does not exist. Schema changes go through a PR that P1 merges.

> **Note for P3:** the kanban columns in
> [`ui_client/static/app.js`](ui_client/static/app.js) and
> [`ui_client/agui/src/state/types.ts`](ui_client/agui/src/state/types.ts) mirror an
> assumed `OpportunityStatus` enum: `new, qualified, packaged, scheduled, published,
> outreach_sent, replied, negotiating, won, lost` (with `parked` folded into `lost`).
> If the real enum differs, change that one list and the board follows.

## Frozen HTTP

Every route below requires the signed tenant header except the auth routes on 8000. Build clients with `shared.http_clients.client()` and it is attached for you.

- `POST /opportunities/search` and `POST /tools/find_opportunities` → `{opportunities[]}` (8081)
- `POST /cdr/run` → `{run_id}` ; `GET /cdr/runs/{id}/events` SSE ; `POST /cdr/runs/{id}/stop` (8084)
- `POST /pipeline/upsert` ; `GET /pipeline/opportunities` ; `POST /pipeline/calendar` ; `POST /tools/persist_and_schedule` ; `GET|POST /pipeline/memory` (8082)
- `GET|POST /pipeline/rag` ; `GET|POST /pipeline/engagement` ; `GET|POST /pipeline/analytics` (8082) — **new**, replacing the `demo/maya/*.json` reads
- `POST /engagement/ingest` ; `POST /engagement/process_week` ; `GET /engagement/inbox` (8083)
- `POST /ag-ui` AG-UI event stream (8084)
- `GET /mcp/tools` ; `POST /mcp/call` (8085)

`POST /engagement/replay_maya_week2` is **gone**. It read one hardcoded file for one persona; `POST /engagement/process_week` does the same job for whichever creator is on the request.

On 8000, the UI client serves `POST /ag-ui` (proxied to 8084 with the tenant header) plus:

| Route | Purpose |
|---|---|
| `POST /auth/signup` · `/auth/login` · `/auth/logout` · `GET /auth/me` | Accounts and sessions |
| `POST /api/profiles` · `GET|POST /api/profile` · `POST /api/profiles/{id}/select` | Onboarding and profile switching |
| `GET /api/opportunities` · `/api/calendar` · `/api/memory` · `/api/inbox` · `/api/artifacts` · `/api/rag_corpus` | Board data, scoped to the signed-in creator |
| `GET /api/runs` · `GET /api/runs/{id}/events` | Run history and replay |
| `POST /api/dev/simulate_week` | Generates a synthetic week so the adapt loop is demonstrable. Disabled when `CREATORLOOP_ENV=production` |

## Stack

See [`STACK.md`](STACK.md) for the kick-off slide mapping and where each item is visible on screen.

- **Harness:** LangGraph (graphs) · DeepAgents (`CDRRootAgent`) · Claude Agent SDK (optional specialist)
- **Protocols:** MCP (tools) · AG-UI (CopilotKit UI) · OTEL (traces)
- **Runtime:** Groq locally · AWS Bedrock AgentCore for the recorded/deployed demo
- **Data:** Postgres (SQLAlchemy + Alembic), one schema in [`shared/models.py`](shared/models.py) · per-creator RAG corpus in `rag_documents`

## Demo story

See [`demo/DEMO_SCRIPT.md`](demo/DEMO_SCRIPT.md) for the 3:00 cue sheet, timed against `DEMO_SPEED=0.6`.

Week 1: run campaign, parallel research, critique fails the hook at 0.42, rewrite passes at 0.86, outreach sends itself.
Week 2: Laksa Lab replied interested; noodle posts do 3.1× median and dessert does 0.4×; memory promotes that to a rule and the next week's plan changes on its own.

To drive that live rather than from fixtures: `python scripts/seed_demo_user.py`, sign in, **Run campaign**, then `POST /api/dev/simulate_week` for week 2.

## Schema changes

```bash
alembic revision --autogenerate -m "what changed"   # after editing shared/models.py
alembic upgrade head
```

One migration history for the whole repo. Five chains in a shared repo is the fastest route to a `main` nobody can run.
