"""LLM helper. One client for every named agent on the team.

Agents are async, so these are async. Three entry points:

    text   = await chat(system, user)
    data   = await chat_json(system, user)            # -> dict
    model  = await chat_model(System, user, Schema)   # -> validated pydantic

PROVIDERS
---------
Two backends, chosen by `LLM_PROVIDER` (default `groq`):

    groq      Groq-hosted open models. Fast and free; the local default.
    bedrock   Claude on Amazon Bedrock. The AWS story, and what the hackathon
              sandbox account bills against.

Agents never pick. They call `chat_json` / `complete_json` and get whichever
provider is configured, so switching is one env var and no code change.

`available()` is False when the *selected* provider has no credentials. Agents
should check it and fall back to fixtures rather than raising -- the demo must
never hard-fail.

COMPATIBILITY
-------------
Agents written against the earlier fixture-first API keep working unchanged:

    from shared.llm import USE_FIXTURES, complete_json, fixture_json, seed_opportunities

`complete_json` is a shim over `chat_json`, so those agents inherit the 429
retry and connection-error backoff below for free. New agents should prefer
`chat_model`, which validates output against shared/schemas.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
from typing import Any, Awaitable, Callable, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from shared.fixtures import fixture_json, seed_opportunities
from shared.flags import use_fixtures

load_dotenv()

T = TypeVar("T", bound=BaseModel)

# Module-level constant kept for agents that import it directly. Prefer
# shared.flags.use_fixtures(), which re-reads the env instead of freezing it
# at import time.
USE_FIXTURES = use_fixtures()

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "openai/gpt-oss-20b")
MAX_ATTEMPTS = 3

# Bedrock model ids carry an `anthropic.` prefix. Haiku is the default on
# purpose: the hackathon sandbox is capped at US$20 and revokes access there,
# so the cheapest model that can do the job is the right default. Override with
# BEDROCK_MODEL_ID if a specific agent needs more.
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-haiku-4-5")
# Bedrock inference for these models lives in us-east-1. This is NOT the SSO
# region from the access portal (ap-southeast-1) -- using that one returns a
# wall of `Access denied to bedrock:*`.
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Groq's free tier is 8k tokens/minute. Parallel agent fan-out blows through
# that in one burst, so every call retries on 429 instead of failing the run.
# Bedrock throttles too, so both providers share the same retry path.
RATE_LIMIT_RETRIES = int(os.getenv("GROQ_RATE_LIMIT_RETRIES", "5"))
_RETRY_HINT = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)
# Claude likes to wrap JSON in a markdown fence even when told not to.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMError(RuntimeError):
    """Raised when the model could not produce usable output."""


class JSONModeError(LLMError):
    """Provider-side JSON mode rejected its own output.

    Groq returns 400 `json_validate_failed` when the model runs past max_tokens
    mid-object: the partial JSON never comes back as text, it comes back as a
    BadRequestError. That is not retryable as-is, so it used to kill the run.
    `chat_json` catches this and retries with JSON mode off.
    """


def provider() -> str:
    """`bedrock` or `groq`. Read per call so a service can be flipped live."""
    name = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    return name if name in ("groq", "bedrock") else "groq"


def _env(name: str) -> str | None:
    """Env var, treating an unfilled placeholder as unset.

    .env ships with PASTE_..._HERE placeholders. Without this, `available()`
    reads those as real credentials and every agent tries a doomed signed
    request instead of falling back to fixtures.
    """
    value = (os.getenv(name) or "").strip()
    if not value or value.startswith("PASTE_") or value.startswith("<"):
        return None
    return value


def available() -> bool:
    """True when the selected provider has usable credentials."""
    if provider() == "bedrock":
        # Sandbox credentials are temporary, so the session token is required
        # alongside the key pair -- key + secret alone will not sign a request.
        # A configured profile or role is fine too, hence the aws_profile check.
        return bool(
            (_env("AWS_ACCESS_KEY_ID") and _env("AWS_SECRET_ACCESS_KEY"))
            or _env("AWS_PROFILE")
        )
    return bool(_env("GROQ_API_KEY"))


def _client():
    from groq import AsyncGroq

    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set; call available() first")
    return AsyncGroq(api_key=key)


def _bedrock_client():
    """Messages-API Bedrock client, signed with the sandbox's temporary keys."""
    from anthropic import AsyncAnthropicBedrockMantle

    kwargs: dict[str, Any] = {"aws_region": AWS_REGION}
    # Pass explicitly when present; otherwise let the SDK walk the normal AWS
    # credential chain (profile, role, instance metadata).
    if _env("AWS_ACCESS_KEY_ID"):
        kwargs["aws_access_key"] = _env("AWS_ACCESS_KEY_ID")
        kwargs["aws_secret_key"] = _env("AWS_SECRET_ACCESS_KEY")
        if _env("AWS_SESSION_TOKEN"):
            kwargs["aws_session_token"] = _env("AWS_SESSION_TOKEN")
    elif _env("AWS_PROFILE"):
        kwargs["aws_profile"] = _env("AWS_PROFILE")
    return AsyncAnthropicBedrockMantle(**kwargs)


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Groq tells us how long to wait; fall back to exponential backoff."""
    hint = _RETRY_HINT.search(str(exc))
    if hint:
        return min(float(hint.group(1)) + 0.4, 30.0)
    return min(2.0**attempt, 16.0) + random.uniform(0, 0.5)


def _retryable() -> tuple[type[Exception], ...]:
    """Throttle / transient errors for whichever provider is selected."""
    if provider() == "bedrock":
        from anthropic import APIConnectionError, InternalServerError, RateLimitError
    else:
        from groq import APIConnectionError, InternalServerError, RateLimitError
    return (RateLimitError, APIConnectionError, InternalServerError)


async def _with_rate_limit_retry(call: Callable[[], Awaitable[Any]]) -> Any:
    """Retry through 429s and transient errors. LLMError once retries run out."""
    retryable = _retryable()
    last: Exception | None = None
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return await call()
        except retryable as exc:
            last = exc
            await asyncio.sleep(_retry_delay(exc, attempt))

    raise LLMError(f"{provider()} unavailable after {RATE_LIMIT_RETRIES} retries: {last}")


def _strip_fences(raw: str) -> str:
    """```json { ... } ``` -> { ... }. Claude fences JSON even when told not to."""
    match = _FENCE.match(raw)
    return match.group(1).strip() if match else raw.strip()


async def _complete_raw(
    system: str,
    user: str,
    *,
    model: str | None,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> str:
    """One completion, as raw text, from whichever provider is selected."""
    if provider() == "bedrock":
        # No response_format on the Messages API; the JSON instruction that
        # chat_json adds to the system prompt does that job, and the caller
        # retries on unparseable output.
        resp = await _with_rate_limit_retry(
            lambda: _bedrock_client().messages.create(
                model=model or BEDROCK_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return _strip_fences(text) if json_mode else text.strip()

    from groq import BadRequestError

    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = await _with_rate_limit_retry(
            lambda: _client().chat.completions.create(
                model=model or DEFAULT_MODEL,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                **kwargs,
            )
        )
    except BadRequestError as exc:
        if "json_validate_failed" in str(exc):
            raise JSONModeError(str(exc)) from exc
        raise
    return (resp.choices[0].message.content or "").strip()


async def chat(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    """Plain text completion."""
    return await _complete_raw(
        system,
        user,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=False,
    )


async def chat_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """JSON-mode completion. Retries on unparseable output."""
    system = f"{system}\n\nRespond with a single valid JSON object and nothing else."
    last: Exception | None = None
    json_mode = True

    for attempt in range(MAX_ATTEMPTS):
        try:
            raw = await _complete_raw(
                system,
                user,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
        except JSONModeError as exc:
            # The provider's strict validator rejected a run-on object. Drop
            # to plain completion and ask for something shorter; the system
            # prompt still demands JSON and the parse below still guards it.
            last = exc
            json_mode = False
            user = f"{user}\n\nYour previous reply ran too long and was cut off. Return a single compact JSON object with only the required fields."
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            last = exc
            user = f"{user}\n\nYour previous reply was not valid JSON ({exc}). Return only the JSON object."
            continue

        # This function is typed `-> dict`, and every caller does data.get(...).
        # A model that answers with a bare array or scalar is still valid JSON,
        # so json.loads succeeds and the agent dies later on .get instead.
        if isinstance(parsed, dict):
            return parsed
        last = LLMError(f"expected a JSON object, got {type(parsed).__name__}")
        user = (
            f"{user}\n\nYour previous reply was a JSON {type(parsed).__name__}, not an object. "
            "Wrap it in a single object with named fields."
        )

    raise LLMError(f"no valid JSON after {MAX_ATTEMPTS} attempts: {last}")


async def chat_model(
    system: str,
    user: str,
    schema: type[T],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    agent: str = "",
) -> T:
    """JSON-mode completion validated into a pydantic model from shared.schemas.

    The model sees the real JSON schema, so field names always match the
    frozen contract. Retries feed validation errors back to the model.

    Honours USE_FIXTURES, which it previously did not. Every Opportunity Finder
    agent calls this, so with the flag on they still made live inference calls:
    "fixture mode" burned real quota, and the resulting rate limiting made the
    finder progressively slower and intermittently empty. Pass `agent` so the
    fixture lookup can find the right canned payload.
    """
    if use_fixtures():
        data = fixture_json(agent, user)
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise LLMError(
                f"USE_FIXTURES=1 but the fixture for {agent or 'this agent'} does "
                f"not match {schema.__name__}: {exc}. Add one in shared/fixtures.py."
            ) from exc

    contract = json.dumps(schema.model_json_schema(), indent=2)
    system = f"{system}\n\nMatch this JSON schema exactly:\n{contract}"
    last: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        data = await chat_json(
            system, user, model=model, temperature=temperature, max_tokens=max_tokens
        )
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            last = exc
            user = f"{user}\n\nYour previous reply failed validation:\n{exc}\nFix those fields."

    raise LLMError(f"{schema.__name__} validation failed after {MAX_ATTEMPTS} attempts: {last}")


# --------------------------------------------------------------------------
# Compatibility shim
# --------------------------------------------------------------------------


async def complete_json(system: str, user: str, *, agent: str = "") -> dict[str, Any]:
    """JSON completion. Live by default; fixtures only when explicitly asked.

    This used to fall back to `fixture_json` whenever the provider had no
    credentials or a call failed, so a broken key produced a confident campaign
    built from Maya's canned answers. With real accounts that is a creator in
    Lisbon being shown Singapore laksa opportunities and told they are results.

    Fixtures now require USE_FIXTURES=1, which the test suite sets. Otherwise a
    missing key or an unreachable model raises, the run reports the failure on
    the trace, and nobody is handed someone else's content as their own.
    """
    if use_fixtures():
        return fixture_json(agent, user)

    if not available():
        raise LLMError(
            f"{provider()} has no usable credentials, so {agent or 'this agent'} "
            "cannot run. Set the provider's API key, or set USE_FIXTURES=1 to "
            "run against canned demo data."
        )

    return await chat_json(system, user)


__all__ = [
    "USE_FIXTURES",
    "DEFAULT_MODEL",
    "FAST_MODEL",
    "BEDROCK_MODEL",
    "LLMError",
    "available",
    "provider",
    "chat",
    "chat_json",
    "chat_model",
    "complete_json",
    "fixture_json",
    "seed_opportunities",
]
