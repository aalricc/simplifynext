import { useEffect, useRef } from "react";
import type { Board } from "../state/types";
import { STATUSES, STATUS_ALIAS } from "../state/types";
import { renderCard } from "./cards";

/* Auto-scroll a feed to its newest row without yanking the whole page. */
function useStickToBottom(dep: unknown) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    // Jump, do not animate: rows can land faster than a smooth scroll
    // finishes, and each new one restarts the animation from the top.
    if (el) el.scrollTop = el.scrollHeight;
  }, [dep]);
  return ref;
}

/* ------------------------------------------------------------------ */

export type ProfileChip = { id: string; name: string; handle: string };

export function CampaignBar({
  niche, city, setNiche, setCity, pause, setPause, mode, board, profile, onRun, onStop,
}: {
  niche: string; city: string;
  setNiche: (v: string) => void; setCity: (v: string) => void;
  pause: boolean; setPause: (v: boolean) => void;
  mode: string; board: Board;
  profile: ProfileChip | null;
  onRun: () => void; onStop: () => void;
}) {
  return (
    <header className="campaign-bar">
      <div className="brand">
        <span className="logo">◐</span>
        <div><h1>CreatorLoop</h1><p className="tag">plan · act · adapt</p></div>
      </div>

      <div className="campaign-fields">
        <label>Niche<input value={niche} onChange={(e) => setNiche(e.target.value)} /></label>
        <label>City<input value={city} onChange={(e) => setCity(e.target.value)} /></label>
        <label>Profile
          <div className="profile-chip">
            <span className="avatar">
              {(profile?.name || profile?.handle || "?").trim().charAt(0).toUpperCase()}
            </span>
            <span>
              <b>{profile?.name || "Loading…"}</b>
              <i>{profile?.handle ? `@${profile.handle}` : ""}</i>
            </span>
          </div>
        </label>
      </div>

      <div className="campaign-actions">
        <div className="toggles">
          <label className="switch" title="Optional HITL. Default off - the agents send on their own.">
            <input type="checkbox" checked={pause} onChange={(e) => setPause(e.target.checked)} />
            <span>Pause before send</span>
          </label>
          <span className={`mode-badge ${mode}`}>{mode === "live" ? "live · :8084" : "fixture mode"}</span>
        </div>
        {/* The one required human action. */}
        <button className="run" disabled={board.running} onClick={onRun}>
          {board.week === 2 ? "Run week 2 (replay)" : "Run campaign"}
        </button>
        <button className="stop" disabled={!board.running} onClick={onStop}>Stop</button>
      </div>
    </header>
  );
}

/* The lede. The counts live here rather than in each panel's badge, so the
   first thing read is what happened, not how many rows a panel holds. */
export function RunStatus({ board }: { board: Board }) {
  const cls = board.running ? "running" : board.traces.length ? "done" : "";
  return (
    <div className={`run-status ${cls}`}>
      <span className="dot" />
      <span id="run-status-text">{board.statusLine}</span>
      <span className="spacer" />
      <span id="run-meta">{board.runId ? `run ${board.runId}` : ""}</span>
      <div className="lede-stats">
        <div><span>{board.traces.length}</span><small>agent steps</small></div>
        <div><span>{board.mcp.length}</span><small>tool calls</small></div>
        <div><span>{board.artifacts.length}</span><small>artifacts</small></div>
      </div>
    </div>
  );
}

export function AgentTrace({ board }: { board: Board }) {
  const ref = useStickToBottom(board.traces.length);
  return (
    <div className="panel grow">
      <h2>Agent trace</h2>
      <p className="hint">OTEL span names. Every line is a real named agent, not “AI is thinking”.</p>
      <div className="trace" ref={ref as any}>
        {board.traces.map((t) => (
          <div className={`trace-row ${t.status ?? "running"}`} key={t.key}>
            <div className="row1">
              <span className="agent">{t.agent}</span>
              <span className={`badge ${t.pattern}`}>{t.pattern}</span>
              <span className="svc">{t.service}</span>
            </div>
            <div className="summary">{t.summary}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function McpPanel({ board }: { board: Board }) {
  return (
    <div className="panel">
      <h2>MCP tool calls</h2>
      <ul className="mcp">
        {board.mcp.length === 0 && <li className="empty">No tool calls yet.</li>}
        {board.mcp.map((c) => (
          <li key={c.key}><span className="tool">{c.tool}</span><span className="args">{c.args_summary}</span></li>
        ))}
      </ul>
    </div>
  );
}

export function OpportunityTable({ board }: { board: Board }) {
  return (
    <div className="panel">
      <h2>Opportunities <span className="count">{board.opportunities.length}</span></h2>
      <p className="hint">Ranked by the scorer, filtered to the creator's niche.</p>
      <table className="opps">
        <thead><tr><th className="num">Score</th><th>Title</th><th>Type</th><th>Status</th></tr></thead>
        <tbody>
          {board.opportunities.length === 0 && (
            <tr className="empty"><td colSpan={4}>Run a campaign to fill the board.</td></tr>
          )}
          {board.opportunities.map((o) => {
            const cls = o.score == null ? "lo" : o.score >= 0.8 ? "hi" : o.score >= 0.6 ? "mid" : "lo";
            return (
              // Score leads: the list is sorted by it, so it is the column the
              // eye should land on first.
              <tr key={o.opportunity_id} title={o.rationale ?? ""}>
                <td className="num"><span className={`score ${cls}`}>{o.score == null ? "—" : o.score.toFixed(2)}</span></td>
                <td>{o.title}</td>
                <td><span className={`type-chip ${o.type}`}>{String(o.type).replace("_", " ")}</span></td>
                <td><span className="type-chip">{o.status}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Kanban({ board }: { board: Board }) {
  const buckets = new Map(STATUSES.map(([k]) => [k, [] as typeof board.opportunities]));
  board.opportunities.forEach((o) => {
    const key = STATUS_ALIAS[o.status] ?? o.status;
    buckets.get(key)?.push(o);
  });
  return (
    <div className="panel">
      <h2>Pipeline</h2>
      <p className="hint">Statuses owned by Pipeline Manager (8082).</p>
      <div className="kanban">
        {STATUSES.map(([key, label]) => {
          const items = buckets.get(key) ?? [];
          return (
            <div className={`kcol ${key}`} key={key}>
              <h3><span>{label}</span><span>{items.length || ""}</span></h3>
              {items.map((o) => (
                <div className="kcard" key={o.opportunity_id}>
                  <b>{o.title}</b>{String(o.type).replace("_", " ")}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function CalendarStrip({ board }: { board: Board }) {
  const cal = board.calendar;
  return (
    <div className="panel">
      <h2>The week <span className="count subtle">{cal ? `of ${cal.week_of}` : "—"}</span></h2>
      <p className="hint">Slots written by the pipeline manager.</p>
      <div className="calendar">
        {(cal?.slots ?? []).map((d) => (
          <div className="day" key={d.day}>
            <h3>{d.day}</h3>
            {d.items.map((i, n) => (
              <div className={`slot ${i.kind}`} key={n}><span className="t">{i.time}</span>{i.title}</div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function InboxPanel({ board }: { board: Board }) {
  return (
    <div className="panel">
      <h2>Engagement inbox <span className="count">{board.inbox.length}</span></h2>
      <ul className="inbox">
        {board.inbox.length === 0 && <li className="empty">Nothing inbound yet. Run week 2.</li>}
        {board.inbox.map((m) => (
          <li key={m.id}>
            <div className="from"><b>{m.from}</b><span className={`cls ${m.classification}`}>{m.classification}</span></div>
            <div className="preview">{m.preview}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MemoryPanel({ board }: { board: Board }) {
  return (
    <div className="panel">
      <h2>What we learned <span className="count">{board.memory.length}</span></h2>
      <ul className="memory">
        {board.memory.length === 0 && <li className="empty">Memory is written at the end of a run.</li>}
        {[...board.memory].reverse().map((e) => (
          <li key={e.id}>
            <div>{e.insight}</div>
            <div className="meta"><span>week {e.week}</span><span>{e.source}</span><span>conf {e.confidence}</span></div>
            {e.changed_from && <div className="was">was: <s>{e.changed_from}</s></div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ArtifactDrawer({ board }: { board: Board }) {
  const ref = useStickToBottom(board.artifacts.length);
  return (
    <div className="panel grow">
      <h2>Artifacts</h2>
      <p className="hint">Agent tool calls rendered as components, not chat text.</p>
      <div className="artifacts" ref={ref}>
        {board.artifacts.length === 0 ? (
          <div className="empty-art">
            <span>◫</span>
            <p>When an agent calls a render tool, the card lands here.</p>
          </div>
        ) : (
          board.artifacts.map((a) => (
            <div key={a.toolCallId} style={{ display: "contents" }}>{renderCard(a.name, a.args)}</div>
          ))
        )}
      </div>
    </div>
  );
}
