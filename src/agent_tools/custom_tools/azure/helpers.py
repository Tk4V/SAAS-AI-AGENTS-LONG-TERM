"""Subprocess helpers for the Azure skill server.

Runs `az` CLI commands with service principal credentials injected as
environment variables. Requires the Azure CLI to be installed and on PATH.
Authentication state is set per-subprocess via env vars — no persistent
`~/.azure/` session is needed after `connect_azure` establishes the login.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

DEFAULT_TIMEOUT_SEC = 60


def _az_env(credentials: dict[str, str]) -> dict[str, str]:
    """Copy the process environment and inject service principal credentials."""
    env = os.environ.copy()
    env.update(
        {
            "AZURE_CLIENT_ID": credentials["client_id"],
            "AZURE_CLIENT_SECRET": credentials["client_secret"],
            "AZURE_TENANT_ID": credentials["tenant_id"],
        }
    )
    return env


async def run_az(
    args: list[str],
    credentials: dict[str, str],
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Run ``az <args>`` with service principal env vars injected.

    Appends ``--output json`` automatically unless the caller already
    specified ``--output`` or ``-o``, so output is structured by default.
    On non-zero exit, stderr is returned as a diagnostic string rather than
    raised so the agent can see the error and self-correct.
    """
    effective_args = list(args)
    if "--output" not in effective_args and "-o" not in effective_args:
        effective_args += ["--output", "json"]

    process = await asyncio.create_subprocess_exec(
        "az",
        *effective_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_az_env(credentials),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        process.kill()
        return f"(az command timed out after {timeout_sec}s: az {' '.join(effective_args)})"

    if process.returncode != 0:
        return f"(az error, exit {process.returncode}):\n{stderr.decode()}"

    return stdout.decode()


async def connect_azure(
    credentials: dict[str, str],
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Run ``az login --service-principal`` then ``az account set``.

    Returns a human-readable success or failure message.
    """
    login_result = await run_az(
        [
            "login",
            "--service-principal",
            "-u", credentials["client_id"],
            "-p", credentials["client_secret"],
            "--tenant", credentials["tenant_id"],
            "--output", "none",
        ],
        credentials,
        timeout_sec=timeout_sec,
    )
    if "az error" in login_result:
        return f"Login failed: {login_result}"

    set_result = await run_az(
        [
            "account", "set",
            "--subscription", credentials["subscription_id"],
            "--output", "none",
        ],
        credentials,
        timeout_sec=timeout_sec,
    )
    if "az error" in set_result:
        return f"Login succeeded but setting subscription failed: {set_result}"

    return f"Connected. Subscription {credentials['subscription_id']!r} is active."
