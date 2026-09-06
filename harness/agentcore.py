"""AWS Bedrock — runtime reporting for the CDR service.

Inference itself lives in `shared/llm.py`, which picks a provider from
`LLM_PROVIDER`. This module only reports what is actually running, so
`GET /health` and the demo can be trusted.

AgentCore Memory is not wired up: `put_memory` is a no-op and says so. Do not
describe it as working.
"""

import os
from typing import Any

from shared.flags import use_fixtures
from shared.llm import AWS_REGION, BEDROCK_MODEL, available, provider


def use_bedrock() -> bool:
    """True only when Bedrock is the selected provider AND it can authenticate.

    This used to return True as soon as AWS_ACCESS_KEY_ID existed, which made
    /health claim `bedrock-agentcore` while every agent still called Groq.
    Exporting sandbox keys for any reason was enough to trigger it.
    """
    return provider() == "bedrock" and available()


# Back-compat: older callers imported this name.
use_agentcore = use_bedrock


def get_model_id() -> str:
    return BEDROCK_MODEL


def get_memory_id() -> str | None:
    return os.getenv("BEDROCK_AGENTCORE_MEMORY_ID") or None


def put_memory(memory: dict[str, Any]) -> None:
    """Not implemented. Memory is SQLite in Pipeline Manager + memory.json.

    Left as a seam for AgentCore Memory. It writes nothing today, so nothing
    should claim otherwise.
    """
    return None


def runtime_payload() -> dict[str, Any]:
    """What /health reports. Describes what will actually happen on a run.

    `runtime` is the effective answer, not the requested one: with fixtures on,
    or credentials missing, agents serve canned output no matter which provider
    is selected, and saying otherwise is how a demo claims a stack it isn't on.
    """
    selected = provider()
    if use_fixtures():
        runtime = "fixtures"
    elif not available():
        # NOT "fixtures". complete_json used to fall back to canned output here,
        # which meant a broken key silently served one persona's demo content as
        # another creator's results. It now raises, so a run in this state fails
        # visibly -- and /health has to say that rather than look healthy.
        runtime = "unavailable"
    else:
        runtime = "bedrock" if selected == "bedrock" else "local-groq"

    payload = {
        "runtime": runtime,
        "provider": selected,
        "model_id": BEDROCK_MODEL if selected == "bedrock" else os.getenv("GROQ_MODEL"),
        "region": AWS_REGION if selected == "bedrock" else None,
        "credentials": "ok" if available() else "missing",
        "memory_id": get_memory_id(),
        "agentcore_memory": "not_implemented",
    }

    if selected == "bedrock":
        # Sandbox credentials are temporary and expire every 12 hours. Missing
        # the session token signs a request that fails at call time, which is a
        # miserable thing to discover mid-demo.
        from shared.llm import _env

        payload["session_token"] = "present" if _env("AWS_SESSION_TOKEN") else "absent"
        if payload["session_token"] == "absent" and not _env("AWS_PROFILE"):
            payload["warning"] = (
                "AWS_SESSION_TOKEN is not set. Temporary sandbox credentials "
                "require it; only permanent IAM keys work without one."
            )

    return payload
