"""Database-backed system prompt assembly for agents.

Loads reusable prompt blocks from the prompt_blocks table and assembles
them into complete system prompts by priority order. Shared blocks
(agent_role=None) are combined with role-specific blocks for the
requested agent role.

Results are cached in-memory for 5 minutes to avoid hitting the DB
on every LLM call within a tool loop.
"""

from __future__ import annotations

import time


class PromptAssembler:
    """Builds system prompts from DB-stored PromptBlock rows.

    Loads shared blocks (agent_role=None) plus role-specific blocks
    for the requested agent role. Assembles them by priority order
    into a single string.
    """

    _cache: dict[str, tuple[str, float]] = {}
    CACHE_TTL = 300  # seconds

    @classmethod
    async def for_role(cls, role: str) -> str:
        """Build the complete system prompt for an agent role.

        Loads shared blocks + role-specific blocks from the DB,
        sorts by priority, and joins them into one string. The result
        is cached so repeated calls within the same tool loop don't
        round-trip to the database.
        """
        cache_key = f"prompt:{role}"
        now = time.time()

        if cache_key in cls._cache:
            cached_prompt, cached_at = cls._cache[cache_key]
            if now - cached_at < cls.CACHE_TTL:
                return cached_prompt

        from sqlalchemy import or_, select

        from src.db.models.prompt_block import PromptBlock
        from src.db.session import db

        async with db.session_scope() as session:
            stmt = (
                select(PromptBlock)
                .where(
                    PromptBlock.is_active == True,  # noqa: E712
                    or_(
                        PromptBlock.agent_role == None,  # noqa: E711 -- shared
                        PromptBlock.agent_role == role,
                    ),
                )
                .order_by(PromptBlock.priority)
            )
            result = await session.execute(stmt)
            blocks = result.scalars().all()

        if not blocks:
            # DB is empty or the migration hasn't run yet; return
            # something minimal so the pipeline doesn't crash
            return f"You are the {role} agent on Clyde, an AI development team."

        prompt = "\n\n".join(block.content.strip() for block in blocks)
        cls._cache[cache_key] = (prompt, now)
        return prompt

    @classmethod
    def clear_cache(cls) -> None:
        """Drop all cached prompts (useful after editing blocks or in tests)."""
        cls._cache.clear()
