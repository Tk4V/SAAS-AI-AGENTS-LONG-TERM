"""LangGraph checkpoint storage notes.

LangGraph persistence for Postgres ships its own schema and migration logic.
Tables `checkpoints`, `checkpoint_writes` and `checkpoint_blobs` are created at
application startup by `AsyncPostgresSaver.setup()` and are managed entirely
by the library; we deliberately do not redeclare them here so that schema
upgrades follow library releases rather than our Alembic history.

This module exists as a sentinel: if you find yourself reaching for a
SQLAlchemy model for checkpoints, that is a sign the persistence layer is
being misused. Use the saver from `src.engine.executor` instead.
"""
