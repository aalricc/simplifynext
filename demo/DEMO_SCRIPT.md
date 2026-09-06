# CreatorLoop — 3:00 demo script

Every agent name, badge and status line below is copied from a real run. If a
cue does not match what you see, the run changed — re-check before recording.

## Setup before you hit record

```bash
python scripts/seed_demo_user.py     # once: creates maya / henry / john
DEMO_SPEED=8 ./scripts/run_local.sh  # pacing: ~18s per campaign
```

- `USE_FIXTURES=1` in `.env`. Every take is then identical and finishes in
  seconds. With it off the agents really call Groq — 3-5 minutes, different
  every time, and the scripted critique beats will not fire.
- `DEMO_SPEED` is frames per second on the UI's stream. It slows delivery to
  the browser, not the agents. `8` gives ~18s; drop to `5` for ~28s if you
  narrate slowly. Unset, a campaign finishes in about **one second** — far too
  fast to talk over.
- Window 1600×1000 or wider so all three columns fit.
- Signed out, on `http://localhost:8000`.
- Sign-in for all three demo accounts: `<name>@creatorloop.local` /
  `creatorloop-demo`.

---

## 0:00 — 0:20 · The problem

**On screen:** the landing page, then sign in as `maya@creatorloop.local`.

> "This is Maya. She cooks hawker food in an HDB kitchen for about eight
> thousand people, and she wants three posts a week and one small brand deal.
>
> Every week starts from a blank page — find the trend, write the hook, shoot
> it, caption it, then cold-email a stall owner who has never heard of her. So
> two of the three posts never get made.
>
> The brief asked for something that plans, acts and adapts over time. That's
> the job."

---

## 0:20 — 0:45 · The architecture

**On screen:** the board, idle. Point at the left and right columns.

> "Six services: an Opportunity Finder, a CDR orchestrator running LangGraph
> graphs under a DeepAgents-style root, a Pipeline Manager, an Engagement
> Listener, an MCP tool server, and this UI — all over one Postgres database.
>
> Thirty-five named agents run across them. Not one mega-prompt. Each appears
> on the left by name with the pattern it uses, and those are the OpenTelemetry
> span names, live.
>
> Every creator signs up and gets their own board. The human's job is one click."

---

## 0:45 — 1:20 · Run the campaign — parallel research

**Click `Run campaign`.** Trace rows, in order:

| Cue | Trace row |
|---|---|
| ~0:47 | `CDRRootAgent` · **custom** — "Planning campaign; tools = research/propose/qa/outreach/persist" |
| ~0:49 | `CDRRootAgent` · custom — "Selected [...]" — two opportunities |
| ~0:52 | `ParallelResearch` · **parallel** — "Fan-out: audience, peers, presence, pain" |
| ~0:54 | Four `llm` rows land together: Audience / PeerCreator / PlatformPresence / PainPoint |
| ~0:58 | `AudienceResearchAgent` · **tool** — "retrieve_creator_memory used" |
| ~1:00 | `ParallelResearch` · parallel — "Gather complete" |
| ~1:05 | Opportunity table fills; **research brief** card in the drawer |
| ~1:12 | `ProposalGenerationAgent` · llm — "Draft package ready" |

> "One click. The root agent plans, then delegates to subgraphs as tools.
>
> Watch the fan-out — four research specialists run at once and gather. One of
> them goes out through MCP to retrieve Maya's own past posts. That's the
> retrieval-augmented bit: her history, not generic food advice.
>
> The right column isn't chat. The agent calls a render tool and AG-UI mounts a
> real component — a research brief, then a full content package."

---

## 1:20 — 1:50 · The critique fails, then the rewrite passes

| Cue | Trace row |
|---|---|
| ~1:22 | `RefinementLoop` · **loop** — "QA until pass or max 3 iterations" |
| ~1:25 | `FactCheckerAgent` · loop — **fail**: `['cite or remove calories']` |
| ~1:28 | `VoiceCritiqueAgent` · loop — "pass: voice ok" |
| ~1:30 | `RefinementLoop` · loop — **"Iteration 1 failed; rewriting"** |
| ~1:34 | `DraftWriterAgent` · loop — "Rewrite applied" |
| ~1:38 | `FactCheckerAgent` · loop — "pass: clean" |
| ~1:41 | `RefinementLoop` · loop — **"Passed on iteration 2"** |

**Open the two package cards side by side.**

> "This is the part I care about. The draft says the bowl is three hundred and
> twenty calories. Nobody measured that — it's the kind of number a model
> invents and a creator gets called out for.
>
> The fact checker fails it. Not a score — a structured verdict with a must-fix
> list: cite or remove calories. The loop rewrites, and the new script says 'I
> don't do mystery calorie claims'. Second iteration passes.
>
> Nobody approved that. The loop caught its own bad work and fixed it, and the
> trace shows both attempts."

---

## 1:50 — 2:10 · Outreach and the pipeline

| Cue | Trace row |
|---|---|
| ~1:52 | `OutreachPipeline` · **sequential** — "Strategy → script → email" |
| ~1:56 | `OutreachScriptAgent` · sequential — "Call script drafted" |
| ~2:00 | `PitchEmailAgent` · sequential — "Email + DM drafted" |
| ~2:04 | `CDRRootAgent` · tool — "persist_and_schedule → MCP/P3" |

**Pan to the centre column.**

> "Outreach writes itself — a pitch email to Laksa Lab, a DM, and a thirty-second
> call script for when she walks in.
>
> Then everything persists through MCP to the Pipeline Manager: opportunities on
> the kanban, artifacts in the drawer, and the week booked onto her actual
> filming days. That's plan and act. Now the part most demos skip."

---

## 2:10 — 2:35 · Week 2 — what came back changes the plan

**Trigger week 2.**

> "A week passes. Laksa Lab replied — interested, wants a call. The classifier
> reads it, moves the opportunity from outreached to engaged, and the kanban
> card moves on its own.
>
> Then the analytics land. The laksa post beat her median several times over.
> The dessert test did a fraction of it. Watch the memory panel: 'prefer hawker
> how-tos, deprioritise dessert how-tos' — that is now a stored rule, and it
> biases next week's plan before anyone asks."

---

## 2:35 — 3:00 · Same agents, a different creator

**Sign out. Sign in as `henry@creatorloop.local`. Click `Run campaign`.**

> "And none of that is hardcoded to Maya. Same thirty-five agents, same graph —
> a different account.
>
> Henry opens Pokémon cards. Different opportunities, a different brand target,
> and watch the critique: it fails him for quoting a pull rate the manufacturer
> never published. His own failure mode, not hers.
>
> Plan, act, adapt — for whoever signs up. That's CreatorLoop."

---

## If something breaks mid-record

- **Board empty / bounced to sign-in** — session expired. Sign in again.
- **Run finishes instantly** — `DEMO_SPEED` isn't set. Restart with
  `DEMO_SPEED=8 ./scripts/run_local.sh`.
- **Critique doesn't fail** — `USE_FIXTURES=0`. The scripted beats only fire on
  fixtures; set it to `1` in `.env` and restart.
- **Trace shows "Could not reach the CDR agent"** — the CDR on :8084 is down.
  The board says so rather than hanging; restart the stack.
- **Wrong persona's data** — you're still signed in as someone else. The profile
  chip in the campaign bar tells you who.

## Verify before recording

```bash
curl -s localhost:8084/health     # runtime.runtime should read "fixtures"
python scripts/uat_smoke.py       # 22 checks across all six services
pytest -q                         # 58 tests
```
