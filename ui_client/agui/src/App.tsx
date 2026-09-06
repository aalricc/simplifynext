import { useEffect, useState } from "react";
import { useBoard, runCampaign, stopCampaign } from "./state/store";
import {
  CampaignBar, RunStatus, AgentTrace, McpPanel, OpportunityTable,
  Kanban, CalendarStrip, InboxPanel, MemoryPanel, ArtifactDrawer,
} from "./components/panels";
import type { ProfileChip } from "./components/panels";
import { setThread } from "./agui/agent";

export default function App() {
  const board = useBoard();
  const [niche, setNiche] = useState("");
  const [city, setCity] = useState("");
  const [pause, setPause] = useState(false);
  const [mode, setMode] = useState("live");
  const [profile, setProfile] = useState<ProfileChip | null>(null);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((cfg) => {
        setMode(cfg.mode);
        setPause(!!cfg.pause_before_send);
      })
      .catch(() => setMode("direct"));

    // The board is behind a login: no session means nothing to draw, and no
    // profile means onboarding was never finished.
    fetch("/api/profile")
      .then((r) => {
        if (r.status === 401) { window.location.href = "/signin"; return null; }
        if (r.status === 409) { window.location.href = "/onboarding"; return null; }
        return r.json();
      })
      .then((p) => {
        if (!p) return;
        setNiche(p.niche ?? "");
        setCity(p.city ?? "");
        setProfile({ id: p.id, name: p.name, handle: p.handle });
        setThread(p.id);
      })
      .catch(() => { /* leave the fields empty rather than guessing */ });
  }, []);

  return (
    <>
      <CampaignBar
        niche={niche} city={city} setNiche={setNiche} setCity={setCity}
        pause={pause} setPause={setPause} mode={mode} board={board}
        profile={profile}
        onRun={() => runCampaign({ niche, city, pauseBeforeSend: pause })}
        onStop={stopCampaign}
      />
      <RunStatus board={board} />

      {/* Two columns, matching the static board: what the week became on the
          left, the evidence and the AG-UI artifacts in the rail. */}
      <main className="board">
        <section className="col col-main">
          <OpportunityTable board={board} />
          <Kanban board={board} />
          <CalendarStrip board={board} />
          <div className="two-up">
            <InboxPanel board={board} />
            <MemoryPanel board={board} />
          </div>
        </section>

        <section className="col col-rail">
          <AgentTrace board={board} />
          <McpPanel board={board} />
          <ArtifactDrawer board={board} />
        </section>
      </main>
    </>
  );
}
