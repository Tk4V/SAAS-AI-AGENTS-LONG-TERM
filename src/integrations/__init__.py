"""Third-party integrations layer.

Public surface lives in `_shared/` (framework) and per-provider folders
(`github/`, future `slack/`, `jira/`, ...). Import directly from those
modules; this package-level `__init__.py` is intentionally empty so callers
do not get a circular dependency through a fat re-export.
"""
