"""GitHub REST API client.

Inherits `BaseApiClient` for token resolution and request mechanics, then
layers GitHub specifics: `application/vnd.github+json` accept type and the
api-version header. Token revocation does not live here — it has its own
non-standard shape (DELETE with Basic auth) and is wired into
`OAuthProviderConfig.custom_revoker` via `github/revoker.py`.

Methods are typed where it matters. JSON shapes returned by GitHub are
left as `dict[str, Any]` because the agent and route layers use only a
small subset of fields and we do not want to model the full GitHub schema.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.config import Settings, get_settings
from src.integrations._shared.api_base import BaseApiClient
from src.integrations._shared.kinds import IntegrationKind
from src.integrations._shared.token_resolver import TokenResolver
from src.integrations.github.git_ops import RepoCoordinates


class GitHubApiClient(BaseApiClient):
    API_VERSION = "2022-11-28"

    def __init__(
        self,
        *,
        user_id: int,
        token_resolver: TokenResolver,
        http_client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        super().__init__(
            kind=IntegrationKind.GITHUB,
            user_id=user_id,
            token_resolver=token_resolver,
            base_url=self._settings.github_api_base,
            http_client=http_client,
        )

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
        }

    async def list_repos(self, *, per_page: int = 100) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                "/user/repos",
                params={
                    "per_page": per_page,
                    "page": page,
                    "sort": "updated",
                    "direction": "desc",
                },
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

    async def list_branches(
        self, *, coordinates: RepoCoordinates, per_page: int = 100
    ) -> list[str]:
        branches: list[str] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                f"/repos/{coordinates.full_name}/branches",
                params={"per_page": per_page, "page": page},
            )
            batch = response.json()
            if not batch:
                break
            branches.extend(b["name"] for b in batch)
            if len(batch) < per_page:
                break
            page += 1
        return branches

    async def create_pull_request(
        self,
        *,
        coordinates: RepoCoordinates,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/repos/{coordinates.full_name}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        )
        data = response.json()
        return {
            "number": data["number"],
            "url": data["html_url"],
            "head_branch": data["head"]["ref"],
            "base_branch": data["base"]["ref"],
        }

    async def find_open_pr(
        self, *, coordinates: RepoCoordinates, head: str
    ) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            f"/repos/{coordinates.full_name}/pulls",
            params={"head": f"{coordinates.owner}:{head}", "state": "open"},
        )
        data = response.json()
        if not data:
            return None
        pr = data[0]
        return {
            "number": pr["number"],
            "url": pr["html_url"],
            "head_branch": pr["head"]["ref"],
            "base_branch": pr["base"]["ref"],
        }

    async def fetch_workflow_run_logs(
        self, *, coordinates: RepoCoordinates, run_id: int
    ) -> str:
        response = await self._request(
            "GET",
            f"/repos/{coordinates.full_name}/actions/runs/{run_id}/logs",
            follow_redirects=True,
        )
        return response.text
