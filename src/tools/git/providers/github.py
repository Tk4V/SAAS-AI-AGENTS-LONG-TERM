"""GitHub implementation of `GitProvider`.

OAuth flow: the frontend sends the user to `authorization_url(...)`, GitHub
redirects back to our callback with a `code`, and we trade that code for an
access token via `exchange_code_for_token(...)`. Tokens are then encrypted
with `TokenCipher` before being stored on `ProjectRepo`.

Repository operations:
- `clone` is a shallow `git clone --depth=N` into a caller-supplied directory.
- `push_branch` runs `git push` against the remote with the token in the URL.
- PR creation and CI log fetching go through the GitHub REST API over httpx.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from urllib.parse import urlencode

import httpx
import structlog
from git import GitCommandError, Repo

from src.common.exceptions import ExternalServiceError
from src.common.retry import RetryPolicy, RetryPresets
from src.config import Settings, get_settings
from src.db.models.project import GitProviderKind
from src.tools.git.provider import (
    ClonedRepo,
    GitProvider,
    PullRequestInfo,
    RepoCoordinates,
)


class GitHubProvider(GitProvider):
    kind = GitProviderKind.GITHUB

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    DEFAULT_SCOPES = "repo,workflow"

    _URL_PATTERNS = (
        re.compile(r"^https?://(?:[^@]+@)?github\.com/(?P<owner>[^/]+)/(?P<name>[^/.]+?)(?:\.git)?/?$"),
        re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<name>[^/.]+?)(?:\.git)?$"),
    )

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._retry = retry_policy or RetryPresets.for_github()
        self._logger = structlog.get_logger("clyde.git.github")

    def parse_repo_url(self, url: str) -> RepoCoordinates:
        for pattern in self._URL_PATTERNS:
            match = pattern.match(url.strip())
            if match:
                return RepoCoordinates(
                    owner=match.group("owner"), name=match.group("name")
                )
        raise ExternalServiceError(
            f"URL {url!r} does not look like a GitHub repository.",
        )

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._settings.github_oauth_client_id.get_secret_value(),
            "redirect_uri": redirect_uri,
            "scope": self.DEFAULT_SCOPES,
            "state": state,
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code_for_token(
        self, *, code: str, redirect_uri: str
    ) -> str:
        payload = {
            "client_id": self._settings.github_oauth_client_id.get_secret_value(),
            "client_secret": self._settings.github_oauth_client_secret.get_secret_value(),
            "code": code,
            "redirect_uri": redirect_uri,
        }
        headers = {"Accept": "application/json"}

        response = await self._retry.run(
            self._http.post, self.TOKEN_URL, data=payload, headers=headers
        )
        response.raise_for_status()
        body = response.json()

        token = body.get("access_token")
        if not token:
            raise ExternalServiceError(
                "GitHub did not return an access token.",
                details={"github_response": body},
            )
        return token

    async def clone(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        branch: str,
        destination: Path,
        depth: int = 1,
    ) -> ClonedRepo:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)

        url = f"https://x-access-token:{token}@github.com/{coordinates.full_name}.git"

        def _clone() -> Repo:
            return Repo.clone_from(
                url,
                to_path=str(destination),
                branch=branch,
                depth=depth,
                single_branch=True,
            )

        try:
            repo = await asyncio.to_thread(_clone)
        except GitCommandError as exc:
            raise ExternalServiceError(
                f"git clone of {coordinates.full_name} failed.",
                details={"stderr": exc.stderr},
            ) from exc

        head_commit = repo.head.commit.hexsha
        self._logger.info(
            "git.clone.completed",
            repo=coordinates.full_name,
            branch=branch,
            head=head_commit,
            depth=depth,
        )
        return ClonedRepo(
            coordinates=coordinates,
            local_path=destination,
            branch=branch,
            head_commit=head_commit,
        )

    async def push_branch(
        self,
        *,
        repo_path: Path,
        branch: str,
        token: str,
    ) -> None:
        def _push() -> None:
            repo = Repo(str(repo_path))
            origin = repo.remote("origin")
            coords = self.parse_repo_url(next(origin.urls))
            authed_url = (
                f"https://x-access-token:{token}@github.com/{coords.full_name}.git"
            )
            with origin.config_writer as cw:
                cw.set("url", authed_url)
            origin.push(refspec=f"{branch}:{branch}")

        try:
            await asyncio.to_thread(_push)
        except GitCommandError as exc:
            raise ExternalServiceError(
                f"git push of {branch} failed.",
                details={"stderr": exc.stderr},
            ) from exc

    async def create_pull_request(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequestInfo:
        url = f"{self._settings.github_api_base}/repos/{coordinates.full_name}/pulls"
        payload = {"title": title, "body": body, "head": head, "base": base}
        response = await self._retry.run(
            self._http.post,
            url,
            json=payload,
            headers=self._auth_headers(token),
        )
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"GitHub PR creation failed for {coordinates.full_name}.",
                details={"status": response.status_code, "body": response.text},
            )
        data = response.json()
        return PullRequestInfo(
            number=data["number"],
            url=data["html_url"],
            head_branch=data["head"]["ref"],
            base_branch=data["base"]["ref"],
        )

    async def fetch_workflow_run_logs(
        self,
        *,
        coordinates: RepoCoordinates,
        token: str,
        run_id: int,
    ) -> str:
        url = (
            f"{self._settings.github_api_base}/repos/"
            f"{coordinates.full_name}/actions/runs/{run_id}/logs"
        )
        response = await self._retry.run(
            self._http.get,
            url,
            headers=self._auth_headers(token),
            follow_redirects=True,
        )
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"Cannot fetch CI logs for run {run_id}.",
                details={"status": response.status_code, "body": response.text[:500]},
            )
        return response.text

    async def list_repos(self, *, token: str, per_page: int = 100) -> list[dict]:
        """Fetch repositories the authenticated user has access to."""
        repos: list[dict] = []
        page = 1
        while True:
            url = (
                f"{self._settings.github_api_base}/user/repos"
                f"?per_page={per_page}&page={page}&sort=updated&direction=desc"
            )
            response = await self._retry.run(
                self._http.get, url, headers=self._auth_headers(token),
            )
            if response.status_code >= 400:
                raise ExternalServiceError(
                    "Failed to fetch repositories from GitHub.",
                    details={"status": response.status_code},
                )
            batch = response.json()
            if not batch:
                break
            for repo in batch:
                repos.append({
                    "full_name": repo["full_name"],
                    "url": repo["html_url"],
                    "default_branch": repo.get("default_branch", "main"),
                    "private": repo.get("private", False),
                    "description": repo.get("description") or "",
                })
            if len(batch) < per_page:
                break
            page += 1
        return repos

    async def revoke_token(self, *, token: str) -> None:
        """Delete the token via GitHub's OAuth Apps endpoint.

        Uses Basic auth with client_id:client_secret. A 204 means the token is
        gone; a 404 means GitHub already considered it invalid, which we treat
        as success because our caller's intent has been satisfied either way.
        """
        client_id = self._settings.github_oauth_client_id.get_secret_value()
        client_secret = self._settings.github_oauth_client_secret.get_secret_value()
        url = f"{self._settings.github_api_base}/applications/{client_id}/token"

        response = await self._retry.run(
            self._http.request,
            "DELETE",
            url,
            json={"access_token": token},
            auth=(client_id, client_secret),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if response.status_code not in (204, 404):
            raise ExternalServiceError(
                "GitHub refused to revoke the OAuth token.",
                details={"status": response.status_code, "body": response.text[:500]},
            )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
