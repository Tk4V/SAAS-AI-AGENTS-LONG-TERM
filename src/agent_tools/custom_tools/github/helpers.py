"""HTTP helpers for the GitHub skill server.

Lower-level ``httpx`` calls used by the tool classes in ``tools.py``.
Kept separate so the tool layer stays declarative and the HTTP layer can
be tested in isolation (e.g. with ``respx``).
"""

from __future__ import annotations

from typing import Any

import httpx

from src.config.settings import get_settings

DEFAULT_TIMEOUT_SEC = 30
DEFAULT_TAIL_LINES_PER_JOB = 200


async def fetch_failed_run_logs(
    *,
    token: str,
    repo_full_name: str,
    run_id: int,
    max_lines_per_job: int = DEFAULT_TAIL_LINES_PER_JOB,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """List failed jobs for the run, fetch tail logs for each, concatenate.

    Returns a multi-section plain-text string — one section per failed
    job — already trimmed to the last ``max_lines_per_job`` lines so a
    multi-MB log does not blow up the prompt. Errors at any step are
    surfaced as diagnostic text rather than raised.
    """
    api_base = get_settings().github_api_base
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    runs_url = f"{api_base}/repos/{repo_full_name}/actions/runs/{run_id}/jobs"

    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
        try:
            jobs_response = await client.get(runs_url, headers=headers)
            jobs_response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"(failed to list jobs for run {run_id}: {exc})"

        jobs = jobs_response.json().get("jobs", [])
        failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]

        if not failed_jobs:
            return (
                f"(no failed jobs in run {run_id}; the run may have been "
                f"cancelled, timed out, or failed at the workflow-startup "
                f"stage before any job executed)"
            )

        sections = [
            await _fetch_one_job_log(
                client=client,
                headers=headers,
                api_base=api_base,
                repo_full_name=repo_full_name,
                job=job,
                max_lines=max_lines_per_job,
            )
            for job in failed_jobs
        ]

    return "\n\n".join(sections)


async def _fetch_one_job_log(
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    api_base: str,
    repo_full_name: str,
    job: dict[str, Any],
    max_lines: int,
) -> str:
    """Fetch and tail the log text for a single failed job."""
    job_id = job["id"]
    job_name = job.get("name", "unnamed")
    log_url = f"{api_base}/repos/{repo_full_name}/actions/jobs/{job_id}/logs"

    try:
        log_response = await client.get(log_url, headers=headers)
        log_response.raise_for_status()
    except httpx.HTTPError as exc:
        body = f"(failed to fetch logs for job {job_id}: {exc})"
    else:
        lines = log_response.text.splitlines()
        body = "\n".join(lines[-max_lines:])

    return f"### Job: {job_name} (id={job_id})\n{body}"
