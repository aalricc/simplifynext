import { useSyncExternalStore } from "react";
import type { BaseEvent } from "@ag-ui/client";
import { cdrAgent } from "../agui/agent";
import type {
  Board, Trace, Opportunity, InboxMessage, MemoryEntry, CalendarWeek, Pattern,
} from "./types";

const empty = (): Board => ({
  running: false,
  runId: null,
  week: 1,
  statusLine: "Idle. One click starts the week.",
  traces: [],
  mcp: [],
  opportunities: [],
  calendar: null,
  inbox: [],
  memory: [],
  artifacts: [],
});

let board: Board = empty();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const set = (patch: Partial<Board>) => { board = { ...board, ...patch }; emit(); };

let seq = 0;
const pendingTools = new Map<string, { name: string; raw: string }>();

/* ------------------------------------------------------------------ */
/* reducers                                                            */
/* ------------------------------------------------------------------ */

function upsertOpportunities(items: Opportunity[]) {
  const byId = new Map(board.opportunities.map((o) => [o.opportunity_id, o]));
  items.forEach((o) => byId.set(o.opportunity_id, { ...byId.get(o.opportunity_id), ...o }));
  set({
    opportunities: [...byId.values()].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
  });
}

function applyStatuses(updates: { opportunity_id: string; status: string }[]) {
  const byId = new Map(board.opportunities.map((o) => [o.opportunity_id, { ...o }]));
  updates.forEach((u) => {
    const existing = byId.get(u.opportunity_id);
    if (existing) existing.status = u.status;
    else
      byId.set(u.opportunity_id, {
        opportunity_id: u.opportunity_id,
        title: u.opportunity_id,
        type: "—",
        score: null,
        status: u.status,
      });
  });
  set({ opportunities: [...byId.values()].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)) });
}

function mergeById<T extends { id: string }>(existing: T[], incoming: T[]): T[] {
  const byId = new Map(existing.map((e) => [e.id, e]));
  incoming.forEach((e) => byId.set(e.id, e));
  return [...byId.values()];
}

/** CUSTOM events carry the board panels; TOOL_CALL events carry the cards. */
function handleCustom(name: string, value: any) {
  switch (name) {
    case "agent_trace": {
      const t: Trace = { key: `t${seq++}`, ...value };
      set({ traces: [...board.traces, t] });
      break;
    }
    case "mcp_call":
      set({ mcp: [...board.mcp, { key: `m${seq++}`, ...value }] });
      break;
    case "opportunities":
      upsertOpportunities(value.opportunities ?? []);
      break;
    case "pipeline":
      applyStatuses(value.updates ?? []);
      break;
    case "engagement":
      set({ inbox: mergeById<InboxMessage>(board.inbox, value.messages ?? []) });
      break;
    case "memory":
      set({ memory: mergeById<MemoryEntry>(board.memory, value.entries ?? []) });
      break;
    default:
      break;
  }
}

function handle(event: BaseEvent) {
  const e = event as any;
  switch (e.type) {
    case "RUN_STARTED":
      set({ running: true, runId: e.runId ?? board.runId });
      break;

    case "RUN_FINISHED":
    case "RUN_ERROR":
      set({
        running: false,
        week: board.week === 1 ? 2 : 1,
        statusLine:
          e.type === "RUN_ERROR"
            ? `Run error: ${e.message ?? "unknown"}`
            : board.week === 1
              ? "Week 1 done. Week 2 replays what came back."
              : board.statusLine,
      });
      break;

    case "CUSTOM":
      handleCustom(e.name, e.value ?? {});
      break;

    case "TOOL_CALL_START":
      pendingTools.set(e.toolCallId, { name: e.toolCallName, raw: "" });
      break;

    case "TOOL_CALL_ARGS": {
      const t = pendingTools.get(e.toolCallId);
      if (t) t.raw += e.delta ?? "";
      break;
    }

    case "TOOL_CALL_END": {
      const t = pendingTools.get(e.toolCallId);
      pendingTools.delete(e.toolCallId);
      if (!t) break;
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(t.raw || "{}");
      } catch {
        console.warn("unparseable tool args for", t.name);
      }
      set({ artifacts: [...board.artifacts, { toolCallId: e.toolCallId, name: t.name, args }] });
      if (t.name === "render_calendar_week") set({ calendar: args as unknown as CalendarWeek });
      break;
    }

    case "TEXT_MESSAGE_CONTENT":
      if (e.delta) set({ statusLine: e.delta });
      break;

    default:
      break;
  }
}

/* Subscribe once at module load so no event is missed between renders. */
cdrAgent.subscribe({
  onEvent: ({ event }: { event: BaseEvent }) => handle(event),
});

/* ------------------------------------------------------------------ */
/* public API                                                          */
/* ------------------------------------------------------------------ */

export interface CampaignInput {
  niche: string;
  city: string;
  pauseBeforeSend: boolean;
}

export async function runCampaign(input: CampaignInput) {
  const week = board.week;
  if (week === 1) {
    // Week 1 starts clean; week 2 must build on week 1's board.
    board = { ...empty(), week: 1 };
  }
  const runId = `run_${Date.now().toString(36)}`;
  set({
    running: true,
    runId,
    statusLine: week === 1 ? "Running campaign…" : "Replaying week 2…",
  });

  try {
    await cdrAgent.runAgent({
      runId,
      // No profile here: the server takes it from the session cookie. A
      // client-supplied profile would be an untrusted tenant claim.
      forwardedProps: {
        week,
        niche: input.niche,
        city: input.city,
        pause_before_send: input.pauseBeforeSend,
      },
    });
  } catch (err) {
    set({ running: false, statusLine: `Stream failed: ${(err as Error).message}` });
    return;
  }
  if (board.running) set({ running: false });
}

export function stopCampaign() {
  cdrAgent.abortRun();
  set({ running: false, statusLine: "Run stopped by the human. Nothing sent." });
}

export function useBoard(): Board {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => listeners.delete(l); },
    () => board,
    () => board,
  );
}

export type { Pattern };
