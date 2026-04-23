"""Picks the right Anthropic model for each agent role."""

from __future__ import annotations

from typing import ClassVar

from src.common.exceptions import AppError
from src.config import Settings, get_settings


class UnknownModelAliasError(AppError):
    code = "unknown_model_alias"
    http_status = 500


class ModelRouter:

    DEFAULT_ROLE_TO_ALIAS: ClassVar[dict[str, str]] = {
        "developer": "sonnet",
        "publisher": "haiku",
        "qa_engineer": "sonnet",
    }

    def __init__(self, *, settings: Settings | None = None, role_to_alias: dict[str, str] | None = None, fallback_alias: str = "sonnet") -> None:
        self._settings = settings or get_settings()
        self._role_to_alias = role_to_alias or dict(self.DEFAULT_ROLE_TO_ALIAS)
        self._fallback_alias = fallback_alias

    def model_for(self, role: str) -> str:
        alias = self._role_to_alias.get(role, self._fallback_alias)
        return self._resolve_alias(alias)

    def _resolve_alias(self, alias: str) -> str:
        match alias:
            case "opus":
                return self._settings.anthropic_model_opus
            case "sonnet":
                return self._settings.anthropic_model_sonnet
            case "haiku":
                return self._settings.anthropic_model_haiku
        raise UnknownModelAliasError(f"Unknown model alias {alias!r}.")
