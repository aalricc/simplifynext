#!/usr/bin/env python3
"""Prove the Bedrock path works, before you need it.

    python scripts/check_bedrock.py

Reads `.env`, reports exactly what is configured, then makes one real (tiny)
Bedrock call and prints what came back. Costs a fraction of a cent.

Sandbox credentials expire every 12 hours, so run this right before a demo or a
recording rather than trusting that it worked yesterday.

Exit code 0 means a real model replied.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


def mask(value: str | None) -> str:
    if not value:
        return f"{RED}not set{RESET}"
    if value.startswith("PASTE_") or value.startswith("<"):
        return f"{RED}placeholder — not filled in{RESET}"
    if len(value) <= 10:
        return f"{GREEN}set{RESET} {DIM}({value}){RESET}"
    return f"{GREEN}set{RESET} {DIM}({value[:6]}…{value[-4:]}, {len(value)} chars){RESET}"


async def main() -> int:
    from shared.llm import _env, available, provider

    print(f"\n{DIM}--- configuration ---{RESET}")
    print(f"  LLM_PROVIDER           {os.getenv('LLM_PROVIDER', 'groq')}")
    print(f"  AWS_REGION             {mask(os.getenv('AWS_REGION'))}")
    print(f"  AWS_ACCESS_KEY_ID      {mask(os.getenv('AWS_ACCESS_KEY_ID'))}")
    print(f"  AWS_SECRET_ACCESS_KEY  {mask(os.getenv('AWS_SECRET_ACCESS_KEY'))}")
    print(f"  AWS_SESSION_TOKEN      {mask(os.getenv('AWS_SESSION_TOKEN'))}")
    print(f"  BEDROCK_MODEL_ID       {mask(os.getenv('BEDROCK_MODEL_ID'))}")

    problems = []
    if provider() != "bedrock":
        problems.append(
            "LLM_PROVIDER is not 'bedrock'. This script still tests the Bedrock "
            "call, but your app will use Groq until you change it."
        )
    if not _env("AWS_ACCESS_KEY_ID") or not _env("AWS_SECRET_ACCESS_KEY"):
        print(f"\n{RED}Cannot test:{RESET} AWS key id and secret are required.")
        print("  AWS access portal -> Accounts -> expand your account -> Access keys -> Option 1")
        return 1
    if not _env("AWS_SESSION_TOKEN") and not _env("AWS_PROFILE"):
        problems.append(
            "AWS_SESSION_TOKEN is not set. Sandbox credentials are temporary and "
            "will not sign without it."
        )

    for p in problems:
        print(f"\n{YELLOW}warning:{RESET} {p}")

    print(f"\n{DIM}--- one real call ---{RESET}")
    os.environ["LLM_PROVIDER"] = "bedrock"
    os.environ["USE_FIXTURES"] = "0"

    if not available():
        print(f"{RED}available() is False{RESET} — credentials are not usable.")
        return 1

    try:
        from shared.llm import chat_json

        reply = await chat_json(
            "Reply with JSON only: {\"ok\": true, \"model\": \"<the model you are>\"}.",
            "Say hello.",
        )
    except Exception as exc:
        print(f"{RED}FAILED{RESET}  {type(exc).__name__}: {exc}\n")
        blob = str(exc).lower()
        if "expired" in blob or "token" in blob or "security" in blob:
            print("  Looks like expired credentials. Re-copy all three values from")
            print("  the AWS access portal — they expire every 12 hours.")
        elif "denied" in blob or "accessdenied" in blob:
            print("  Access denied. Check AWS_REGION is Bedrock's region (us-east-1),")
            print("  NOT the portal's sign-in region (ap-southeast-1), and that the")
            print(f"  model {os.getenv('BEDROCK_MODEL_ID')} is enabled for your account.")
        elif "not found" in blob or "validation" in blob:
            print(f"  Check BEDROCK_MODEL_ID={os.getenv('BEDROCK_MODEL_ID')} is a valid id")
            print("  and is enabled in this region.")
        return 1

    print(f"{GREEN}OK{RESET}  Bedrock replied: {reply}")
    print(f"\n  Model:  {os.getenv('BEDROCK_MODEL_ID')}")
    print(f"  Region: {os.getenv('AWS_REGION')}")
    print(f"\n  {DIM}Set LLM_PROVIDER=bedrock and USE_FIXTURES=0 to run the app on it.{RESET}")
    print(f"  {DIM}Budget: a full campaign is 60-80 calls. Watch the sandbox cap.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
