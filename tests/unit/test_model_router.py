"""Tests for the ModelRouter role-to-model mapping.

Verifies the cost/quality tiers: Opus for planning agents, Sonnet for coding
agents, Haiku for lightweight tasks, and Sonnet as fallback for unknown roles.
"""

from __future__ import annotations

from src.tools.llm.router import ModelRouter


class TestModelRouter:
    async def test_tech_lead_gets_opus(self, test_settings):
        router = ModelRouter(settings=test_settings)
        model = router.model_for("tech_lead")
        assert model == test_settings.anthropic_model_opus

    async def test_architect_gets_opus(self, test_settings):
        router = ModelRouter(settings=test_settings)
        model = router.model_for("architect")
        assert model == test_settings.anthropic_model_opus

    async def test_senior_developer_gets_sonnet(self, test_settings):
        router = ModelRouter(settings=test_settings)
        model = router.model_for("senior_developer")
        assert model == test_settings.anthropic_model_sonnet

    async def test_release_manager_gets_haiku(self, test_settings):
        router = ModelRouter(settings=test_settings)
        model = router.model_for("release_manager")
        assert model == test_settings.anthropic_model_haiku

    async def test_unknown_role_gets_fallback_sonnet(self, test_settings):
        """Roles not in the mapping should fall back to Sonnet."""
        router = ModelRouter(settings=test_settings)
        model = router.model_for("totally_new_agent")
        assert model == test_settings.anthropic_model_sonnet

    async def test_custom_role_mapping(self, test_settings):
        """Users can override the default mapping at construction time."""
        custom = {"tech_lead": "haiku"}
        router = ModelRouter(settings=test_settings, role_to_alias=custom)
        model = router.model_for("tech_lead")
        assert model == test_settings.anthropic_model_haiku
