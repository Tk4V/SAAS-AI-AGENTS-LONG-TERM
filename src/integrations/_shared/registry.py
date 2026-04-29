"""ProviderCatalog — explicit registry of every integration the app knows about.

Why explicit imports and not auto-discovery: a junior dev should be able to
open this file and see *every* registered provider in one place. Magic
auto-loaders make grep harder and push the failure mode to runtime.

To add a provider: append the import + the constant to `_PROVIDERS`. Add the
matching `IntegrationKind` enum value. That is the entire registration step.
"""

from __future__ import annotations

from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.exceptions import ProviderConfigError
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind
from src.integrations.github.config import GITHUB
from src.integrations.google.config import GOOGLE
from src.integrations.jira.config import JIRA
from src.integrations.slack.config import SLACK

_PROVIDERS: tuple[OAuthProviderConfig, ...] = (
    GITHUB,
    JIRA,
    GOOGLE,
    SLACK,
    # Add new providers here. One line per provider.
    # DISCORD,
    # SENTRY,
)


class ProviderCatalog:
    """Lookup of provider configs keyed by `IntegrationKind`."""

    def __init__(
        self,
        providers: tuple[OAuthProviderConfig, ...] = _PROVIDERS,
    ) -> None:
        self._by_kind: dict[IntegrationKind, OAuthProviderConfig] = {}
        for provider in providers:
            if provider.kind in self._by_kind:
                raise ProviderConfigError(
                    f"Duplicate registration for {provider.kind.value!r}."
                )
            self._by_kind[provider.kind] = provider

    def get(self, kind: IntegrationKind) -> OAuthProviderConfig:
        try:
            return self._by_kind[kind]
        except KeyError as exc:
            raise ProviderConfigError(
                f"No provider registered for {kind.value!r}. "
                f"Add it in src/integrations/_shared/registry.py."
            ) from exc

    def all(self) -> tuple[OAuthProviderConfig, ...]:
        return tuple(self._by_kind.values())

    def by_category(
        self, category: IntegrationCategory
    ) -> tuple[OAuthProviderConfig, ...]:
        return tuple(p for p in self._by_kind.values() if p.category is category)

    def __contains__(self, kind: IntegrationKind) -> bool:
        return kind in self._by_kind
