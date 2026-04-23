"""Tests for the ModelRouter role-to-model mapping."""

from __future__ import annotations

from src.tools.llm.router import ModelRouter


class TestModelRouter:
    async def test_developer_gets_sonnet(self, test_settings):
        router = ModelRouter(settings=test_settings)
        assert router.model_for("developer") == test_settings.anthropic_model_sonnet

    async def test_publisher_gets_haiku(self, test_settings):
        router = ModelRouter(settings=test_settings)
        assert router.model_for("publisher") == test_settings.anthropic_model_haiku

    async def test_qa_engineer_gets_sonnet(self, test_settings):
        router = ModelRouter(settings=test_settings)
        assert router.model_for("qa_engineer") == test_settings.anthropic_model_sonnet

    async def test_unknown_role_gets_fallback(self, test_settings):
        router = ModelRouter(settings=test_settings)
        assert router.model_for("new_agent") == test_settings.anthropic_model_sonnet
