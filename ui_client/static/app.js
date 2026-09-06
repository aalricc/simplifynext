/* CreatorLoop board client.
 *
 * Speaks AG-UI over SSE from POST /ag-ui. The server decides whether those
 * events came from a fixture file or from the live CDR agent on :8084 - this
 * file never needs to know, which is what makes the day-5 swap a config change.
 */
(function () {
  "use strict";

  const { TOOLS, fallback, esc } = window.CL;
  const $ = (id) => document.getElementById(id);

  const EMPTY_ARTIFACT_LIST = '<div class="empty-art"><span>◫</span><p>Scripts, captions and messages land here as they’re written.</p></div>';
  const EMPTY_ARTIFACT_DETAIL = '<div class="empty-art"><span>◫</span><p>Select an item on the left to see the full draft.</p></div>';

  /* Pipeline statuses are owned by Pipeline Manager. Mirror of the
     OpportunityStatus enum in shared/schemas.py - if P3 changes it, change
     this map and nothing else. Each status gets a plain-language label and
     is bucketed into one of four creator-facing stages for the board. */
  const STATUS_META = {
    new:           { label: "New idea",      group: "new" },
    qualified:     { label: "Reviewed",       group: "ready" },
    packaged:      { label: "Content ready",  group: "ready" },
    scheduled:     { label: "Scheduled",      group: "ready" },
    published:     { label: "Posted",         group: "ready" },
    outreach_sent: { label: "Reached out",    group: "reaching" },
    replied:       { label: "They replied",   group: "reaching" },
    negotiating:   { label: "In talks",       group: "reaching" },
    won:           { label: "Deal won",       group: "done" },
    lost:          { label: "Passed",         group: "done" },
    parked:        { label: "Passed",         group: "done" },
  };
  const GROUPS = [
    ["new", "New ideas"],
    ["ready", "Getting ready"],
    ["reaching", "Reaching out"],
    ["done", "Done"],
  ];
  const statusLabel = (s) => (STATUS_META[s] && STATUS_META[s].label) || s;
  const statusGroup = (s) => (STATUS_META[s] && STATUS_META[s].group) || "new";

  const state = {
    runId: null, running: false, nextWeek: 1, profileId: null,
    opps: new Map(), traces: 0, mcp: 0,
    memory: new Map(), inbox: new Map(), startedAt: 0,
    pendingTools: new Map(), oppSignatures: new Map(), contentPreview: [],
    artifactList: [], selectedArtifactId: null, artifactSeq: 0,
  };

  /* ------------------------------------------------------------------ */
  /* tabs                                                                 */
  /* ------------------------------------------------------------------ */

  function positionIndicator(tabEl) {
    const nav = $("tabs");
    const indicator = $("tab-indicator");
    if (!tabEl || !nav) return;
    const navBox = nav.getBoundingClientRect();
    const tabBox = tabEl.getBoundingClientRect();
    indicator.style.width = tabBox.width + "px";
    indicator.style.transform = `translateX(${tabBox.left - navBox.left}px)`;
  }

  function goToTab(name, { focus = false } = {}) {
    const tabBtn = document.querySelector(`.tab[data-tab="${name}"]`);
    if (!tabBtn) return;
    document.querySelectorAll(".tab").forEach((b) => {
      const active = b === tabBtn;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
      // Roving tabindex: one Tab press enters the tablist, then arrows move
      // between tabs. Without this every tab is its own tab stop, which is
      // the behaviour the ARIA tabs pattern explicitly replaces.
      b.tabIndex = active ? 0 : -1;
    });
    if (focus) tabBtn.focus();
    document.querySelectorAll(".tabpanel").forEach((p) => {
      const active = p.dataset.tab === name;
      p.classList.toggle("active", active);
      if (active) {
        p.classList.remove("entering");
        // restart the entrance animation every time the tab is opened
        void p.offsetWidth;
        p.classList.add("entering");
      }
    });
    positionIndicator(tabBtn);
  }

  document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => goToTab(b.dataset.tab))
  );

  // Arrow / Home / End across the tabs. The markup declared role="tablist"
  // from the start but nothing implemented the keyboard half of that contract,
  // so the tabs announced themselves as tabs and then behaved like links.
  document.getElementById("tabs")?.addEventListener("keydown", (e) => {
    const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
    if (!keys.includes(e.key)) return;
    const tabs = [...document.querySelectorAll(".tab")];
    const i = tabs.findIndex((t) => t.classList.contains("active"));
    if (i < 0) return;
    e.preventDefault();
    const next =
      e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1
      : e.key === "ArrowRight" ? (i + 1) % tabs.length
      : (i - 1 + tabs.length) % tabs.length;
    goToTab(tabs[next].dataset.tab, { focus: true });
  });
  document.querySelectorAll(".kpi-tile").forEach((b) =>
    b.addEventListener("click", () => goToTab(b.dataset.goto))
  );
  window.addEventListener("resize", () => {
    positionIndicator(document.querySelector(".tab.active"));
  });
  // fonts/layout can settle a frame after load; re-measure once more
  window.addEventListener("load", () => positionIndicator(document.querySelector(".tab.active")));
  positionIndicator(document.querySelector(".tab.active"));

  /* ------------------------------------------------------------------ */
  /* panels                                                              */
  /* ------------------------------------------------------------------ */

  function pulseKpi(id) {
    const valueEl = $(id);
    const tile = valueEl && valueEl.closest(".kpi-tile");
    if (!tile) return;
    tile.classList.add("pulse");
    setTimeout(() => tile.classList.remove("pulse"), 500);
  }

  function bump(ids, n) {
    (Array.isArray(ids) ? ids : [ids]).forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.textContent = n;
    });
    if (Array.isArray(ids)) ids.filter((id) => id.startsWith("kpi-")).forEach(pulseKpi);
  }

  /* Technical activity log - tucked into a closed <details>, off by default.
     Still wired up so nothing breaks if you open it; just not part of the
     creator-facing flow. */
  function addTrace(t) {
    const ul = $("trace");
    if (!ul) return;
    const li = document.createElement("li");
    li.className = t.status || "running";
    li.innerHTML = `
      <div class="row1">
        <span class="agent">${esc(t.agent)}</span>
        <span class="badge ${esc(t.pattern)}">${esc(t.pattern)}</span>
        <span class="svc">${esc(t.service || "")}</span>
      </div>
      <div class="summary">${esc(t.summary)}</div>`;
    ul.appendChild(li);
    ul.scrollTop = ul.scrollHeight;
    bump("trace-count", ++state.traces);
  }

  function addMcp(c) {
    const ul = $("mcp-list");
    if (!ul) return;
    if (state.mcp === 0) ul.innerHTML = "";
    const li = document.createElement("li");
    li.innerHTML = `<span class="tool">${esc(c.tool)}</span><span class="args">${esc(c.args_summary || "")}</span>`;
    ul.appendChild(li);
    ul.scrollTop = ul.scrollHeight;
    bump("mcp-count", ++state.mcp);
  }

  function upsertOpps(items) {
    items.forEach((o) => {
      const prev = state.opps.get(o.opportunity_id) || {};
      state.opps.set(o.opportunity_id, { ...prev, ...o });
    });
    drawOpps();
    drawKanban();
    drawOverviewOpps();
  }

  function setStatuses(updates) {
    updates.forEach((u) => {
      const o = state.opps.get(u.opportunity_id);
      if (o) o.status = u.status;
      else state.opps.set(u.opportunity_id, { opportunity_id: u.opportunity_id, title: u.opportunity_id, status: u.status, type: "—", score: null });
    });
    drawOpps();
    drawKanban();
    drawOverviewOpps();
  }

  function drawOpps() {
    const listEl = $("opp-list");
    const rows = [...state.opps.values()].sort((a, b) => (b.score || 0) - (a.score || 0));
    if (!rows.length) return;
    listEl.innerHTML = rows
      .map((o) => {
        const s = o.score;
        const cls = s == null ? "lo" : s >= 0.8 ? "hi" : s >= 0.6 ? "mid" : "lo";
        // flash only rows whose score or status actually changed since the
        // last render - a full-list flash on every redraw would cry wolf
        const sig = `${s}|${o.status}`;
        const changed = state.oppSignatures.has(o.opportunity_id) && state.oppSignatures.get(o.opportunity_id) !== sig;
        state.oppSignatures.set(o.opportunity_id, sig);
        return `<div class="idea-row${changed ? " flash" : ""}" title="${esc(o.rationale || "")}">
          <span class="type-chip ${esc(o.type)}">${esc(String(o.type).replace("_", " "))}</span>
          <b class="idea-row-title">${esc(o.title)}</b>
          <span class="score ${cls}">${s == null ? "—" : s.toFixed(2)}</span>
          <span class="stage-chip stage-${esc(statusGroup(o.status))}">${esc(statusLabel(o.status))}</span>
        </div>`;
      })
      .join("");
    bump(["opp-count", "kpi-opps"], rows.length);
  }

  function drawKanban() {
    const board = $("kanban");
    const buckets = new Map(GROUPS.map(([k]) => [k, []]));
    state.opps.forEach((o) => {
      const key = statusGroup(o.status);
      if (buckets.has(key)) buckets.get(key).push(o);
    });
    board.innerHTML = GROUPS.map(([key, label]) => {
      const items = buckets.get(key);
      const cards = items
        .map((o) => `<div class="kcard"><b>${esc(o.title)}</b>${esc(statusLabel(o.status))}</div>`)
        .join("");
      return `<div class="kcol ${esc(key)}">
        <h3><span>${esc(label)}</span><span>${items.length || ""}</span></h3>
        <div class="kcol-cards">${cards}</div>
      </div>`;
    }).join("");
  }

  function drawOverviewOpps() {
    const el = $("ov-opps");
    if (!el) return;
    const rows = [...state.opps.values()].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 4);
    if (!rows.length) {
      el.innerHTML = '<p class="empty">Run a campaign to see this week’s ideas.</p>';
      return;
    }
    el.innerHTML = rows
      .map(
        (o) => `
        <div class="ov-item">
          <span class="type-chip ${esc(o.type)}">${esc(String(o.type).replace("_", " "))}</span>
          <div class="ov-item-body">
            <b>${esc(o.title)}</b>
            <p>${esc(o.rationale || o.why_now || "")}</p>
          </div>
        </div>`
      )
      .join("");
  }

  function drawCalendar(a) {
    $("cal-week").textContent = "of " + a.week_of;
    const slots = a.slots || [];
    const html = slots
      .map((d) => {
        const items = (d.items || [])
          .map((i) => `<div class="slot ${esc(i.kind)}"><span class="t">${esc(i.time)}</span>${esc(i.title)}</div>`)
          .join("");
        return `<div class="day"><h3>${esc(d.day)}</h3>${items}</div>`;
      })
      .join("");
    $("calendar").innerHTML = html;
    const ovCal = $("ov-calendar");
    if (ovCal) ovCal.innerHTML = html;
    const total = slots.reduce((n, d) => n + (d.items || []).length, 0);
    bump(["cal-count", "kpi-cal"], total);
  }

  function drawInbox(messages) {
    messages.forEach((m) => state.inbox.set(m.id, m));
    const ul = $("inbox");
    ul.innerHTML = [...state.inbox.values()]
      .map(
        (m) => `<li>
          <div class="from"><b>${esc(m.from)}</b><span class="cls ${esc(m.classification)}">${esc(m.classification)}</span></div>
          <div class="preview">${esc(m.preview)}</div>
        </li>`
      )
      .join("");
    bump(["inbox-count", "kpi-inbox"], state.inbox.size);
    drawOverviewInbox();
  }

  function drawOverviewInbox() {
    const el = $("ov-inbox");
    if (!el) return;
    const items = [...state.inbox.values()].slice(-4).reverse();
    if (!items.length) {
      el.innerHTML = '<p class="empty">Nothing inbound yet.</p>';
      return;
    }
    el.innerHTML = items
      .map(
        (m) => `
        <div class="ov-item">
          <span class="cls ${esc(m.classification)}">${esc(m.classification)}</span>
          <div class="ov-item-body">
            <b>${esc(m.from)}</b>
            <p>${esc(m.preview)}</p>
          </div>
        </div>`
      )
      .join("");
  }

  function drawMemory(entries) {
    entries.forEach((e) => state.memory.set(e.id, e));
    const ul = $("memory");
    ul.innerHTML = [...state.memory.values()].reverse()
      .map(
        (e) => `<li>
          <div>${esc(e.insight)}</div>
          <div class="meta"><span>week ${esc(e.week)}</span><span>${esc(e.source)}</span><span>conf ${esc(e.confidence)}</span></div>
          ${e.changed_from ? `<div class="was">was: <s>${esc(e.changed_from)}</s></div>` : ""}
        </li>`
      )
      .join("");
    bump(["memory-count", "kpi-memory"], state.memory.size);
    drawOverviewMemory();
  }

  function drawOverviewMemory() {
    const el = $("ov-memory");
    if (!el) return;
    const entries = [...state.memory.values()].reverse().slice(0, 4);
    if (!entries.length) {
      el.innerHTML = '<li class="empty">This fills in after week 2.</li>';
      return;
    }
    el.innerHTML = entries
      .map(
        (e) => `<li>
          <div>${esc(e.insight)}</div>
          ${e.changed_from ? `<div class="was">was: <s>${esc(e.changed_from)}</s></div>` : ""}
        </li>`
      )
      .join("");
  }

  /* ---------------- content tab: list + detail ---------------- */

  function renderArtifactRow(entry) {
    const listEl = $("artifact-list");
    if (state.artifactList.length === 1) listEl.innerHTML = "";
    const row = document.createElement("div");
    row.className = `content-row ${entry.data.cls || ""}`;
    row.dataset.id = entry.id;
    row.innerHTML = `
      <span class="row-ico">${entry.data.ico}</span>
      <div class="row-body">
        <div class="row-title">${esc(entry.data.title)}</div>
        <div class="row-meta">${esc(entry.data.meta)}</div>
      </div>`;
    row.addEventListener("click", () => selectArtifact(entry.id));
    listEl.appendChild(row);
    listEl.scrollTop = listEl.scrollHeight;
  }

  function selectArtifact(id) {
    state.selectedArtifactId = id;
    document.querySelectorAll(".content-row").forEach((r) => r.classList.toggle("selected", r.dataset.id === id));
    const entry = state.artifactList.find((e) => e.id === id);
    if (!entry) return;
    const pane = $("artifact-detail");
    pane.innerHTML = `
      <div class="detail-enter">
        <div class="detail-header"><span class="ico">${entry.data.ico}</span><h1>${esc(entry.data.title)}</h1></div>
        <div class="detail-body">${entry.data.body}</div>
      </div>`;
    pane.scrollTop = 0;
  }

  function mountArtifact(name, args) {
    const render = TOOLS[name];
    const data = render ? render(args) : fallback(name, args);
    const id = `art-${state.artifactSeq++}`;
    const entry = { id, name, data };
    state.artifactList.push(entry);
    renderArtifactRow(entry);
    if (!state.selectedArtifactId) selectArtifact(id);
    bump(["artifact-count", "kpi-artifacts"], state.artifactList.length);
  }

  function addContentPreview(a) {
    state.contentPreview.unshift(a);
    if (state.contentPreview.length > 4) state.contentPreview.length = 4;
    drawOverviewContent();
  }

  function drawOverviewContent() {
    const el = $("ov-content");
    if (!el) return;
    if (!state.contentPreview.length) {
      el.innerHTML = '<p class="empty">Content shows up here once the app writes it.</p>';
      return;
    }
    el.innerHTML = state.contentPreview
      .map((a) => {
        const platform = (a.platform || "tiktok").toLowerCase();
        return `
        <div class="media-preview ov-content-item">
          <div class="media-frame ${esc(platform)}">
            <span class="plat">${esc(platform)}</span>
            <span class="play">▶</span>
            <span class="dur">${esc(a.duration_s || 60)}s</span>
          </div>
          <div class="media-meta">
            <b>${esc(a.hook || "")}</b>
            <p>${esc(a.caption || "")}</p>
          </div>
        </div>`;
      })
      .join("");
  }

  /* ------------------------------------------------------------------ */
  /* AG-UI event handling                                                */
  /* ------------------------------------------------------------------ */

  const CUSTOM = {
    agent_trace: addTrace,
    mcp_call: addMcp,
    opportunities: (v) => upsertOpps(v.opportunities || []),
    pipeline: (v) => setStatuses(v.updates || []),
    engagement: (v) => drawInbox(v.messages || []),
    memory: (v) => drawMemory(v.entries || []),
    calendar: () => {},
  };

  function handle(ev) {
    switch (ev.type) {
      case "RUN_STARTED":
        setRunning(true, ev.runId);
        break;

      case "RUN_FINISHED":
        setRunning(false);
        break;

      case "RUN_ERROR":
        status("Run error: " + (ev.message || "unknown"), "done");
        setRunning(false);
        break;

      case "CUSTOM": {
        const fn = CUSTOM[ev.name];
        if (fn) fn(ev.value || {});
        break;
      }

      case "TOOL_CALL_START":
        state.pendingTools.set(ev.toolCallId, { name: ev.toolCallName, raw: "" });
        break;

      case "TOOL_CALL_ARGS": {
        const t = state.pendingTools.get(ev.toolCallId);
        if (t) t.raw += ev.delta || "";
        break;
      }

      case "TOOL_CALL_END": {
        const t = state.pendingTools.get(ev.toolCallId);
        state.pendingTools.delete(ev.toolCallId);
        if (!t) break;
        let args = {};
        try { args = JSON.parse(t.raw || "{}"); }
        catch (e) { console.warn("bad tool args for", t.name, e); }
        mountArtifact(t.name, args);
        if (t.name === "render_calendar_week") drawCalendar(args);
        if (t.name === "render_content_package") addContentPreview(args);
        break;
      }

      case "TEXT_MESSAGE_CONTENT":
        status(ev.delta, state.running ? "running" : "done");
        break;

      default:
        break;
    }
  }

  /* ------------------------------------------------------------------ */
  /* run control                                                         */
  /* ------------------------------------------------------------------ */

  function status(text, cls) {
    $("run-status-text").textContent = text;
    $("run-status").className = "run-status " + (cls || "");
  }

  function setRunning(on, runId) {
    state.running = on;
    if (runId) state.runId = runId;
    $("btn-run").disabled = on;
    $("btn-stop").disabled = !on;
    if (on) {
      state.startedAt = Date.now();
      tick();
    } else {
      $("run-meta").textContent =
        `${state.artifactList.length} pieces of content · ${((Date.now() - state.startedAt) / 1000).toFixed(1)}s`;
      state.nextWeek = state.nextWeek === 1 ? 2 : 1;
      $("btn-run").textContent = state.nextWeek === 2 ? "Run week 2 (replay)" : "Run campaign";
      if (state.nextWeek === 2) status("Week 1 done. Week 2 replays what came back.", "done");
    }
  }

  function tick() {
    if (!state.running) return;
    $("run-meta").textContent = `Working · ${((Date.now() - state.startedAt) / 1000).toFixed(1)}s`;
    setTimeout(tick, 200);
  }

  async function run() {
    const week = state.nextWeek;
    if (week === 1) {
      // Fresh week 1 clears the board; week 2 must build on week 1's state.
      state.opps.clear(); state.memory.clear(); state.inbox.clear();
      state.oppSignatures.clear(); state.contentPreview = [];
      state.artifactList = []; state.selectedArtifactId = null; state.artifactSeq = 0;
      state.traces = 0; state.mcp = 0;
      $("trace").innerHTML = "";
      $("artifact-list").innerHTML = EMPTY_ARTIFACT_LIST;
      $("artifact-detail").innerHTML = EMPTY_ARTIFACT_DETAIL;
      $("mcp-list").innerHTML = '<li class="empty">No tool calls yet.</li>';
      [
        "trace-count", "artifact-count", "mcp-count", "opp-count", "inbox-count", "memory-count",
        "kpi-artifacts", "kpi-opps", "kpi-inbox", "kpi-memory", "kpi-cal", "cal-count",
      ].forEach((i) => bump(i, 0));
      drawOverviewOpps(); drawOverviewInbox(); drawOverviewMemory(); drawOverviewContent();
    }

    const runId = `run_${Date.now().toString(36)}`;
    state.runId = runId;
    setRunning(true, runId);
    status(week === 1 ? "Planning your week…" : "Checking what came back…", "running");

    // The server overrides profile from the session cookie; sending it here
    // only keeps the client's own correlation ids readable.
    const payload = {
      runId, week,
      threadId: state.profileId || runId,
      niche: $("f-niche").value,
      city: $("f-city").value,
      pause_before_send: $("f-pause").checked,
    };

    let res;
    try {
      res = await fetch("/ag-ui", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      status("Cannot reach the UI server on :8000.", "done");
      setRunning(false);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const payloadText = line.slice(5).trim();
          if (!payloadText) continue;
          try { handle(JSON.parse(payloadText)); }
          catch (e) { console.warn("bad SSE frame", payloadText, e); }
        }
      }
    }

    if (state.running) setRunning(false);
  }

  async function stop() {
    $("btn-stop").disabled = true;
    status("Stopping…", "running");
    await fetch("/api/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ runId: state.runId }),
    }).catch(() => {});
  }

  /* ------------------------------------------------------------------ */

  async function boot() {
    drawKanban();
    try {
      const cfg = await (await fetch("/api/config")).json();
      const badge = $("mode-badge");
      badge.textContent = cfg.mode === "live" ? "Live" : "Demo mode";
      badge.className = "mode-badge " + cfg.mode;
      badge.title = cfg.mode === "live"
        ? `Streaming from ${cfg.cdr_agui_url}`
        : "Replaying a saved demo run - no API keys needed";
      $("f-pause").checked = !!cfg.pause_before_send;
    } catch (e) {
      $("mode-badge").textContent = "offline";
    }
    // The board is behind a login now: no session means no data to show, and
    // no profile means onboarding was never finished.
    try {
      const res = await fetch("/api/profile");
      if (res.status === 401) { location.href = "/signin"; return; }
      if (res.status === 409) { location.href = "/onboarding"; return; }
      const p = await res.json();
      state.profileId = p.id;
      $("f-niche").value = p.niche || "";
      $("f-city").value = p.city || "";
      $("f-name").textContent = p.name || p.handle || "Your studio";
      $("f-handle").textContent = p.handle ? "@" + p.handle : "";
      $("f-avatar").textContent = (p.name || p.handle || "?").trim()[0].toUpperCase();
    } catch (e) { /* keep the defaults in the markup */ }
  }

  $("btn-run").addEventListener("click", run);
  $("btn-stop").addEventListener("click", stop);
  boot();
})();
