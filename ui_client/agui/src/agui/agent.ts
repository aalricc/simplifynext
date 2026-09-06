import { HttpAgent } from "@ag-ui/client";

/**
 * The single AG-UI connection for the whole app.
 *
 * Points at POST /ag-ui. In dev that proxies to the UI server on :8000, which
 * either replays demo fixtures or forwards to P2's CDR agent on :8084 - so
 * going live is a URL change here (or USE_FIXTURES=0 on the server), and no
 * component has to change.
 */
export const AGUI_URL = import.meta.env.VITE_AGUI_URL ?? "/ag-ui";

// threadId is set from the signed-in profile once /api/profile resolves; the
// server derives tenancy from the session cookie either way, so this is only
// a client-side correlation id.
export const cdrAgent = new HttpAgent({
  url: AGUI_URL,
  agentId: "cdr",
  threadId: "pending",
});

export function setThread(profileId: string): void {
  cdrAgent.threadId = profileId;
}

export const AGENTS = { cdr: cdrAgent };
