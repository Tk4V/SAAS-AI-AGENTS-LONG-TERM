"""Re-export every ORM model so Alembic's autogenerate sees the full metadata.

`env.py` imports this module with `from src.db import models` and relies on
side-effect registration. Adding a new model means appending it here.
"""

from src.db.models.project import ProviderKind, Project, ProjectRepo
from src.db.models.task import Task, TaskStatus
from src.db.models.user_credential import UserOAuthCredential

__all__ = [
    "ProviderKind",
    "Project",
    "ProjectRepo",
    "Task",
    "TaskStatus",
    "UserOAuthCredential",
]
