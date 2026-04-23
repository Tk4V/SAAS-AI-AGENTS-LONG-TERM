"""Reusable instruction blocks for agent system prompts.

Blocks are assembled by PromptAssembler into complete system prompts.
Shared blocks (agent_role=None) apply to all agents. Role-specific
blocks only apply when building a prompt for that role.

This replaces hardcoded prompts in prompts.py files and enables M2
meta-programming: creating new agents means inserting prompt blocks
into this table, not writing Python code.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PromptBlock(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single stored prompt block that PromptAssembler loads at runtime."""

    __tablename__ = "prompt_blocks"
    __table_args__ = (
        UniqueConstraint("key", name="uq_prompt_blocks_key"),
    )

    # Short identifier like "identity", "tech_lead_role", "safety_rules"
    key: Mapped[str] = mapped_column(String(128), nullable=False)

    # The actual prompt text assembled into the system message
    content: Mapped[str] = mapped_column(String, nullable=False)

    # Broad grouping: "shared" | "role" | "output_format" | "tool_rules"
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, default="shared",
    )

    # null = shared block for all agents; a value like "developer" scopes it to that role
    agent_role: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
    )

    # Lower number = appears earlier in the assembled prompt
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    # Soft-delete flag so we can disable blocks without losing them
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
