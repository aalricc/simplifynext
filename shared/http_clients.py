"""Service-to-service HTTP. Every client here carries the tenant headers.

`client()` is the only place an outbound httpx client is built, so a new call
site inherits tenancy instead of having to remember it. If you construct
`httpx.AsyncClient` directly somewhere else, the request arrives with no
profile and the receiving service's database layer raises TenantError.
"""

import os

import httpx

from shared.tenant import outbound_headers

OPPORTUNITY_FINDER_URL = os.getenv("OPPORTUNITY_FINDER_URL", "http://localhost:8081")
PIPELINE_MANAGER_URL = os.getenv("PIPELINE_MANAGER_URL", "http://localhost:8082")
ENGAGEMENT_LISTENER_URL = os.getenv("ENGAGEMENT_LISTENER_URL", "http://localhost:8083")
CDR_URL = os.getenv("CDR_URL", "http://localhost:8084")
MCP_URL = os.getenv("MCP_URL", "http://localhost:8085")


def client(timeout: float = 30.0, profile_id: str | None = None) -> httpx.AsyncClient:
    """An httpx client pre-loaded with this request's tenant headers."""
    return httpx.AsyncClient(timeout=timeout, headers=outbound_headers(profile_id))


async def find_opportunities(payload: dict) -> dict:
    async with client() as c:
        r = await c.post(f"{OPPORTUNITY_FINDER_URL}/tools/find_opportunities", json=payload)
        r.raise_for_status()
        return r.json()


async def persist_and_schedule(payload: dict) -> dict:
    async with client() as c:
        r = await c.post(f"{PIPELINE_MANAGER_URL}/tools/persist_and_schedule", json=payload)
        r.raise_for_status()
        return r.json()


async def get_memory() -> dict:
    async with client() as c:
        r = await c.get(f"{PIPELINE_MANAGER_URL}/pipeline/memory")
        r.raise_for_status()
        return r.json()
