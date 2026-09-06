#!/usr/bin/env python3
"""Frozen-HTTP smoke suite, run against a live stack.

    ./scripts/run_local.sh          # in one terminal
    python scripts/uat_smoke.py     # in another

Replaces the pre-auth PowerShell suite. Every backend route now requires a
signed tenant header, so the suite signs up a throwaway account, completes
onboarding, and drives the real flow -- which also means it exercises the thing
most likely to break: identity surviving all five hops.

Exit code is the number of failed checks.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.tenant import outbound_headers  # noqa: E402

UI = "http://localhost:8000"
FINDER = "http://localhost:8081"
PIPELINE = "http://localhost:8082"
ENGAGEMENT = "http://localhost:8083"
CDR = "http://localhost:8084"
MCP = "http://localhost:8085"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

_passed = 0
_failed: list[tuple[str, str]] = []
_cookies: dict[str, str] = {}
_profile_id = ""


def request(
    url: str,
    method: str = "GET",
    body: dict | None = None,
    *,
    tenant: bool = False,
    cookies: bool = False,
    timeout: float = 30.0,
    raw: bool = False,
):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "*/*"}
    if tenant and _profile_id:
        headers.update(outbound_headers(_profile_id))
    if cookies and _cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in _cookies.items())

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for header in resp.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            _cookies[name.strip()] = rest.split(";")[0]
        payload = resp.read()
    if raw:
        return payload.decode("utf-8", "replace")
    return json.loads(payload or b"{}")


def check(name):
    def wrap(fn):
        global _passed
        try:
            detail = fn()
            _passed += 1
            print(f"  {GREEN}pass{RESET}  {name}  {DIM}{detail or ''}{RESET}")
        except Exception as exc:  # noqa: BLE001 - a smoke suite reports, never raises
            _failed.append((name, str(exc)))
            print(f"  {RED}FAIL{RESET}  {name}\n        {exc}")
        return fn

    return wrap


def main(keep: bool) -> int:
    global _profile_id

    print("\nhealth")

    for label, url in (
        ("ui", UI),
        ("finder", FINDER),
        ("pipeline", PIPELINE),
        ("engagement", ENGAGEMENT),
        ("cdr", CDR),
        ("mcp", MCP),
    ):

        @check(f"{label} /health")
        def _(url=url):
            r = request(f"{url}/health", timeout=5)
            if r.get("status") != "ok":
                raise AssertionError(r)
            if r.get("database") not in (None, "ok"):
                raise AssertionError(f"database {r['database']}: {r.get('error')}")
            return r.get("url", "")

    print("\naccounts")
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    password = "smoke-test-password"

    @check("signup")
    def _():
        request(f"{UI}/auth/signup", "POST", {"email": email, "password": password})
        return email

    @check("unauthenticated board data is refused")
    def _():
        try:
            request(f"{UI}/api/opportunities")
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise AssertionError(f"expected 401, got {exc.code}") from None
            return "401"
        raise AssertionError("served board data with no session")

    @check("onboarding creates a profile")
    def _():
        global _profile_id
        r = request(
            f"{UI}/api/profiles",
            "POST",
            {
                "handle": f"smoke{uuid.uuid4().hex[:6]}",
                "display_name": "Smoke Test",
                "city": "Singapore",
                "niche": "hawker food",
                "platforms": ["tiktok"],
                "best_performing": ["laksa"],
                "worst_performing": ["desserts"],
            },
            cookies=True,
        )
        _profile_id = r["profile"]["id"]
        return _profile_id

    @check("starter data seeded")
    def _():
        docs = request(f"{UI}/api/rag_corpus", cookies=True)["documents"]
        if not docs:
            raise AssertionError("no starter RAG documents")
        return f"{len(docs)} rag documents"

    print("\ntenant enforcement")

    @check("backend refuses a request with no tenant header")
    def _():
        try:
            request(f"{PIPELINE}/pipeline/rag")
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise AssertionError(f"expected 401, got {exc.code}") from None
            return "401"
        raise AssertionError("pipeline served data with no tenant")

    @check("backend refuses a forged token")
    def _():
        req = urllib.request.Request(
            f"{PIPELINE}/pipeline/rag",
            headers={
                "X-CreatorLoop-Profile": _profile_id,
                "X-CreatorLoop-Token": "9999999999.deadbeef",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                raise AssertionError(f"expected 403, got {exc.code}") from None
            return "403"
        raise AssertionError("pipeline accepted a forged token")

    @check("backend accepts a correctly signed header")
    def _():
        docs = request(f"{PIPELINE}/pipeline/rag", tenant=True)["documents"]
        return f"{len(docs)} documents"

    print("\nfrozen HTTP")

    @check("mcp /mcp/tools")
    def _():
        return f"{len(request(f'{MCP}/mcp/tools')['tools'])} tools"

    @check("mcp search_web")
    def _():
        r = request(
            f"{MCP}/mcp/call",
            "POST",
            {"name": "search_web", "arguments": {"query": "singapore laksa", "limit": 2}},
            tenant=True,
        )
        if r.get("error"):
            raise AssertionError(r["error"])
        return "ok"

    @check("finder /tools/find_opportunities")
    def _():
        r = request(
            f"{FINDER}/tools/find_opportunities",
            "POST",
            {
                "profile_id": _profile_id,
                "niche": "hawker food",
                "city": "Singapore",
                "limit": 4,
            },
            tenant=True,
        )
        return f"{len(r['opportunities'])} opportunities, mode={r.get('mode')}"

    @check("pipeline upsert + read back")
    def _():
        oid = f"smoke-{uuid.uuid4().hex[:6]}"
        request(
            f"{PIPELINE}/pipeline/upsert",
            "POST",
            {"id": oid, "type": "trend", "title": "Smoke", "score": 70},
            tenant=True,
        )
        rows = request(f"{PIPELINE}/pipeline/opportunities", tenant=True)["opportunities"]
        if not any(o["id"] == oid for o in rows):
            raise AssertionError("upserted row not returned")
        return oid

    @check("pipeline memory round trip")
    def _():
        request(
            f"{PIPELINE}/pipeline/memory",
            "POST",
            {"wins": ["smoke win"], "losses": [], "next_bias": []},
            tenant=True,
        )
        if request(f"{PIPELINE}/pipeline/memory", tenant=True)["wins"] != ["smoke win"]:
            raise AssertionError("memory did not round trip")
        return "ok"

    print("\ncampaign")
    run_id = f"smoke{uuid.uuid4().hex[:6]}"

    @check("ui POST /ag-ui streams a run")
    def _():
        text = request(
            f"{UI}/ag-ui",
            "POST",
            {"runId": run_id, "week": 1},
            cookies=True,
            timeout=300,
            raw=True,
        )
        frames = [ln for ln in text.splitlines() if ln.startswith("data:")]
        if not frames:
            raise AssertionError("no SSE frames")
        if any('"RUN_ERROR"' in f for f in frames):
            raise AssertionError(next(f for f in frames if '"RUN_ERROR"' in f)[:200])
        if not any('"RUN_FINISHED"' in f for f in frames):
            raise AssertionError("stream never finished")
        # A real campaign emits ~140 frames. A run that selected nothing still
        # emits a dozen and "finishes", so the count is the signal.
        if len(frames) < 40:
            raise AssertionError(
                f"only {len(frames)} frames — the campaign did almost nothing. "
                "Check that the finder is returning opportunities."
            )
        return f"{len(frames)} frames"

    @check("run persisted and replayable")
    def _():
        time.sleep(2)
        runs = request(f"{UI}/api/runs", cookies=True)["runs"]
        if not any(r["run_id"] == run_id for r in runs):
            raise AssertionError("run missing from history")
        events = request(f"{UI}/api/runs/{run_id}/events", cookies=True)["events"]
        if not events:
            raise AssertionError("no persisted frames")
        return f"{len(events)} frames replayable"

    @check("campaign wrote board data")
    def _():
        opps = request(f"{UI}/api/opportunities", cookies=True)["opportunities"]
        arts = request(f"{UI}/api/artifacts", cookies=True)["artifacts"]
        cal = request(f"{UI}/api/calendar", cookies=True)["events"]
        # Lower bounds, not just "not empty". An earlier version of this check
        # passed on a run that produced 11 frames and nothing else, because the
        # finder was silently returning no opportunities -- exactly the failure
        # a smoke suite exists to catch.
        if len(opps) < 2:
            raise AssertionError(f"expected the campaign to work >=2 opportunities, got {len(opps)}")
        kinds = {a["kind"] for a in arts}
        missing = {"package", "outreach", "qa", "brief"} - kinds
        if missing:
            raise AssertionError(f"no artifacts filed for {sorted(missing)}")
        if not cal:
            raise AssertionError("nothing scheduled on the calendar")
        return f"{len(opps)} opps, {len(arts)} artifacts, {len(cal)} calendar events"

    print("\nadapt loop")

    @check("simulate a week, then process it")
    def _():
        r = request(f"{UI}/api/dev/simulate_week", "POST", {}, cookies=True, timeout=120)
        memory = r.get("processed", {}).get("memory", {})
        if not memory.get("next_bias"):
            raise AssertionError(f"no memory derived: {memory}")
        return f"generated={r.get('generated')} bias={memory['next_bias'][0][:44]}"

    if not keep:
        print(f"\n{DIM}throwaway account {email} left in the database{RESET}")

    total = _passed + len(_failed)
    print(f"\n{_passed}/{total} passed")
    for name, err in _failed:
        print(f"  {RED}FAIL{RESET} {name}: {err}")
    return len(_failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="suppress the cleanup note")
    args = parser.parse_args()
    try:
        raise SystemExit(main(args.keep))
    except urllib.error.URLError as exc:
        print(f"\n{RED}Cannot reach the stack.{RESET} Is ./scripts/run_local.sh running?\n  {exc}")
        raise SystemExit(1) from None
