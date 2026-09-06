#!/usr/bin/env python3
"""CreatorLoop UI Client (P4) - port 8000.

Serves the static demo board and exposes POST /ag-ui as an AG-UI SSE stream.

Two modes, one wire protocol:

  fixture (default)  replay demo/fixtures/*.jsonl with authored pacing
  live               proxy straight through to the CDR agent's POST /ag-ui

The browser cannot tell the difference, which is the whole point: the demo
story is built against fixtures now and swaps to P2 on day 5 by flipping
USE_FIXTURES=0. If live mode is on and the CDR service is unreachable, the
stream falls back to fixtures and says so on screen rather than dying.

Standard library only - no pip install needed to run the story.
"""

import http.client
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "static"
FIXTURES = ROOT / "demo" / "fixtures"
MAYA = ROOT / "demo" / "maya"

PORT = int(os.environ.get("UI_PORT", "8000"))
CDR_URL = os.environ.get("CDR_AGUI_URL", "http://localhost:8084/ag-ui")
USE_FIXTURES = os.environ.get("USE_FIXTURES", "1") not in ("0", "false", "False")
PAUSE_BEFORE_SEND = os.environ.get("PAUSE_BEFORE_SEND", "0") not in ("0", "false", "False")
DEMO_SPEED = float(os.environ.get("DEMO_SPEED", "1.0"))
# Live runs are LLM-paced: a whole campaign takes minutes, and long gaps between
# SSE frames are normal. This is the socket timeout for the whole proxied
# stream, not just the connect - a refused connection still fails immediately.
LIVE_TIMEOUT = float(os.environ.get("LIVE_TIMEOUT", "300"))

WEEK_FIXTURES = {
    "1": FIXTURES / "run_events.jsonl",
    "2": FIXTURES / "week2_events.jsonl",
}

# Run ids the client has asked us to stop. The Stop button is optional HITL:
# it never gates the agents, it only ends the stream early.
_cancelled = set()
_cancel_lock = threading.Lock()


# --------------------------------------------------------------------------
# fixture replay
# --------------------------------------------------------------------------

def load_fixture(week):
    path = WEEK_FIXTURES.get(str(week), WEEK_FIXTURES["1"])
    events = []
    if not path.exists():
        return events
    with path.open() as fh:
        for line_no, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw or raw.startswith("//"):
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                print(f"[fixture] skipping {path.name}:{line_no} - {exc}", file=sys.stderr)
    return events


def expand(event, run_id, thread_id):
    """Turn one authored fixture line into real AG-UI protocol events.

    Fixtures use two conveniences so they stay readable by hand:
      TOOL_CALL    with an `args` object  -> START / ARGS(delta) / END
      TEXT_MESSAGE with a `content` string -> START / CONTENT(delta) / END
    Everything else passes through untouched.
    """
    kind = event.get("type")

    if kind == "TOOL_CALL":
        tc_id = event.get("toolCallId") or f"tc_{uuid.uuid4().hex[:8]}"
        name = event.get("toolCallName", "render_unknown")
        args = event.get("args", {})
        return [
            {"type": "TOOL_CALL_START", "toolCallId": tc_id, "toolCallName": name},
            {"type": "TOOL_CALL_ARGS", "toolCallId": tc_id,
             "delta": json.dumps(args, ensure_ascii=False)},
            {"type": "TOOL_CALL_END", "toolCallId": tc_id},
        ]

    if kind == "TEXT_MESSAGE":
        msg_id = event.get("messageId") or f"msg_{uuid.uuid4().hex[:8]}"
        return [
            {"type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": msg_id,
             "delta": event.get("content", "")},
            {"type": "TEXT_MESSAGE_END", "messageId": msg_id},
        ]

    out = {k: v for k, v in event.items() if not k.startswith("_")}
    if kind in ("RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"):
        out["runId"] = run_id
        out["threadId"] = thread_id
    return [out]


def replay(run_id, thread_id, week, speed, emit):
    """Stream a fixture file as AG-UI events. `emit` takes one dict."""
    events = load_fixture(week)
    if not events:
        emit({"type": "RUN_ERROR", "runId": run_id, "threadId": thread_id,
              "message": f"No fixture events for week {week}."})
        return

    for event in events:
        with _cancel_lock:
            if run_id in _cancelled:
                emit({"type": "CUSTOM", "name": "agent_trace", "value": {
                    "agent": "RunSupervisor", "pattern": "custom", "service": "ui",
                    "status": "done", "summary": "Run stopped by the human. Nothing sent."}})
                emit({"type": "RUN_FINISHED", "runId": run_id, "threadId": thread_id})
                return

        delay = event.get("_delay_ms", 400) / 1000.0
        if speed > 0:
            time.sleep(delay / speed)

        for out in expand(event, run_id, thread_id):
            out.setdefault("timestamp", int(time.time() * 1000))
            emit(out)


# --------------------------------------------------------------------------
# live proxy
# --------------------------------------------------------------------------

def proxy_live(body, emit):
    """Forward the run to P2's /ag-ui and relay its SSE frames.

    Returns True if we got a usable stream, False to fall back to fixtures.
    """
    req = urllib.request.Request(
        CDR_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=LIVE_TIMEOUT)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"[live] CDR unreachable at {CDR_URL}: {exc}", file=sys.stderr)
        return False

    # The read loop needs the same guard as the connect. Without it a stalled
    # CDR raised TimeoutError out of the handler: the browser saw a dead stream,
    # the fixture fallback below never ran, and the board hung mid-demo.
    got_any = False
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    emit(json.loads(payload))
                    got_any = True
                except json.JSONDecodeError:
                    continue
    except (TimeoutError, OSError, urllib.error.URLError, http.client.HTTPException) as exc:
        print(f"[live] stream from {CDR_URL} broke: {exc}", file=sys.stderr)
        if got_any:
            # Partway through a real run - say so rather than replaying a
            # fixture on top of live events the board has already drawn.
            emit({"type": "CUSTOM", "name": "agent_trace", "value": {
                "agent": "RunSupervisor", "pattern": "custom", "service": "ui",
                "status": "fail", "summary": f"Live stream ended early: {exc}"}})
            emit({"type": "RUN_FINISHED", "runId": ""})
    return got_any


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CreatorLoopUI/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("UI_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers ---------------------------------------------------------

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, obj, status=200):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, path):
        if not path.exists() or not path.is_file():
            self._json({"error": "not found", "path": str(path.name)}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes ----------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        route = urlparse(self.path)
        path = route.path

        # The landing page is the front door; the board is the app behind it.
        # /index.html still resolves to the board so any bookmark or deep link
        # from before the landing page existed keeps working.
        if path in ("/", "/landing.html"):
            return self._file(STATIC / "landing.html")
        if path in ("/board", "/board/", "/index.html"):
            return self._file(STATIC / "index.html")
        if path == "/api/healthz":
            return self._json({"ok": True, "mode": "fixture" if USE_FIXTURES else "live"})
        if path == "/api/config":
            return self._json({
                "mode": "fixture" if USE_FIXTURES else "live",
                "cdr_agui_url": CDR_URL,
                "pause_before_send": PAUSE_BEFORE_SEND,
                "speed": DEMO_SPEED,
                "profile": "maya",
            })
        if path == "/api/profile":
            return self._file(MAYA / "profile.json")
        if path == "/api/rag_corpus":
            return self._file(MAYA / "rag_corpus.json")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            target = (STATIC / rel).resolve()
            if STATIC.resolve() not in target.parents and target != STATIC.resolve():
                return self._json({"error": "forbidden"}, 403)
            return self._file(target)

        return self._json({"error": "not found", "path": path}, 404)

    def do_POST(self):
        route = urlparse(self.path)
        path = route.path

        if path == "/api/stop":
            body = self._read_body()
            run_id = body.get("runId")
            if run_id:
                with _cancel_lock:
                    _cancelled.add(run_id)
            return self._json({"stopped": run_id})

        if path == "/ag-ui":
            return self._stream_agui(route)

        return self._json({"error": "not found", "path": path}, 404)

    def _stream_agui(self, route):
        body = self._read_body()
        query = parse_qs(route.query)

        # A real AG-UI client (@ag-ui/client HttpAgent) nests run parameters
        # under forwardedProps; the static board posts them flat. Accept both.
        fwd = body.get("forwardedProps") or {}
        run_id = body.get("runId") or fwd.get("runId") or f"run_{uuid.uuid4().hex[:8]}"
        week = str(body.get("week") or fwd.get("week") or query.get("week", ["1"])[0])
        thread_id = body.get("threadId") or fwd.get("threadId") or "maya"
        speed = float(query.get("speed", [DEMO_SPEED])[0])

        with _cancel_lock:
            _cancelled.discard(run_id)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # HTTP/1.1 with neither Content-Length nor chunked encoding gives the
        # client no way to see the response end, so keep-alive left every
        # finished run holding its socket open. Browsers allow ~6 per host, so
        # a few Run clicks would stall the board's own asset requests.
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        self.close_connection = True

        closed = threading.Event()

        def emit(event):
            if closed.is_set():
                return
            try:
                self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ValueError):
                closed.set()

        try:
            if USE_FIXTURES:
                replay(run_id, thread_id, week, speed, emit)
            else:
                emit({"type": "CUSTOM", "name": "agent_trace", "value": {
                    "agent": "RunSupervisor", "pattern": "custom", "service": "ui",
                    "status": "running", "summary": f"Live mode. Streaming from {CDR_URL}."}})
                if not proxy_live({**body, "runId": run_id, "week": week}, emit):
                    emit({"type": "CUSTOM", "name": "agent_trace", "value": {
                        "agent": "RunSupervisor", "pattern": "custom", "service": "ui",
                        "status": "fail",
                        "summary": f"CDR at {CDR_URL} did not answer. Falling back to fixtures."}})
                    replay(run_id, thread_id, week, speed, emit)
        except (BrokenPipeError, ConnectionResetError):
            closed.set()
        except Exception as exc:  # never leave the board spinning on a dead stream
            print(f"[stream] run {run_id} failed: {exc}", file=sys.stderr)
            emit({"type": "RUN_ERROR", "runId": run_id, "message": str(exc)})
        finally:
            with _cancel_lock:
                _cancelled.discard(run_id)


def main():
    mode = "fixture" if USE_FIXTURES else f"live -> {CDR_URL}"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print(f"CreatorLoop UI    http://localhost:{PORT}")
    print(f"  mode            {mode}")
    print(f"  ag-ui endpoint  POST http://localhost:{PORT}/ag-ui")
    print(f"  fixtures        {FIXTURES}")
    print(f"  pause_before_send {'on' if PAUSE_BEFORE_SEND else 'off'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        server.shutdown()


if __name__ == "__main__":
    main()
