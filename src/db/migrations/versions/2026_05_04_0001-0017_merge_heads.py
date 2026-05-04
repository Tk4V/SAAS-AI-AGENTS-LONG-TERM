"""merge heads: add_aws_provider_kind and drop_dead_schema

Revision ID: 0017_merge_heads
Revises: 0016_add_aws_provider_kind, 0016_drop_dead_schema
Create Date: 2026-05-04 00:00:00.000000

"""
from typing import Sequence, Union

revision: str = "0017_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "0016_add_aws_provider_kind",
    "0016_drop_dead_schema",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
