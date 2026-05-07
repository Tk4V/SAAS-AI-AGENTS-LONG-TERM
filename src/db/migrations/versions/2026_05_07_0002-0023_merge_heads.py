"""merge heads: add_memory_graph and add_devops_subagent

Revision ID: 0023_merge_heads
Revises: 0022_add_memory_graph, 0022_add_devops_subagent
Create Date: 2026-05-07 00:02:00.000000

"""
from typing import Sequence, Union

revision: str = "0023_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "0022_add_memory_graph",
    "0022_add_devops_subagent",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
