"""Application context: configuration-derived singletons.

Holds things that are computed once from `Settings` and stay constant for
the lifetime of the process. Nothing here owns a network connection, a
thread, or anything else that needs disposal — that lives in `src/clients.py`.

Members:
- `settings`  — fully resolved `Settings` instance (env, .env, AWS Secrets Manager).
- `cipher`    — `TokenCipher` built from `settings.fernet_key`.

Tests should construct their own `AppContext(settings=...)` and inject it
via the dependent's constructor rather than mutating the global singleton.
"""

from __future__ import annotations

from src.config import Settings, get_settings
from src.utils.crypto import TokenCipher


class AppContext:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._cipher: TokenCipher | None = None

    @property
    def settings(self) -> Settings:
        """Process-wide application settings."""
        return self._settings

    @property
    def cipher(self) -> TokenCipher:
        """Fernet cipher for OAuth token encryption. Built lazily on first access."""
        if self._cipher is None:
            self._cipher = TokenCipher(settings=self._settings)
        return self._cipher


app_context = AppContext()
