"""Opportunity Finder service (P1).

Frozen contract -- CDR calls /tools/find_opportunities as an agent-as-tool:

    POST /opportunities/search      -> {"opportunities": [Opportunity, ...]}
    POST /tools/find_opportunities  -> same
    GET  /opportunities/last        -> last run's result
    GET  /health

Behind those routes runs OpportunityFinderRoot -> OpportunityFinderPipeline
(LangGraph). A failure returns an empty list and a note; it does not substitute
seed data, because seed data belongs to one persona and would be served to
whichever creator happened to ask.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from observability.otel import setup_tracing
from opportunity_finder.agents.root import OpportunityFinderRoot
from opportunity_finder.agents.state import ProfileMissing
from shared.cors import add_cors
from shared.schemas import SearchRequest
from shared.tenant import current_profile

app = FastAPI(title="Opportunity Finder", version="0.3.0")
add_cors(app)
setup_tracing("opportunity_finder")

ROOT = OpportunityFinderRoot()
# Last result per profile. A single module-level `_LAST` leaked one creator's
# search results to whoever asked next.
_LAST: dict[str, dict[str, Any]] = {}


@app.get("/health")
def health() -> dict:
    return {"service": "opportunity_finder", "status": "ok", "agents": 8}


@app.get("/opportunities/last")
def last() -> dict:
    return _LAST.get(current_profile(), {"opportunities": []})


@app.post("/opportunities/search")
@app.post("/tools/find_opportunities")
async def search(req: SearchRequest) -> dict:
    profile = req.profile.model_dump() if req.profile else None
    if profile is None and req.profile_id:
        profile = {"id": req.profile_id, "niche": req.niche, "city": req.city}

    try:
        result = await ROOT.run(
            {
                "profile_id": req.profile_id,
                "profile": profile,
                "niche": req.niche,
                "city": req.city,
                "limit": req.limit,
            }
        )
    except ProfileMissing as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = {
        "opportunities": result["opportunities"],
        "run_id": result["run_id"],
        "mode": result.get("mode", "live"),
        "notes": result.get("notes", []),
    }
    _LAST[current_profile()] = payload
    return payload
