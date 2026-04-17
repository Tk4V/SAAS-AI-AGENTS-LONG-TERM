"""Re-export every ORM model so Alembic's autogenerate sees the full metadata.

`env.py` imports this module with `from src.db import models` and relies on
side-effect registration. Adding a new model means appending it here.
"""

from src.db.models.agent import AgentRecord
from src.db.models.memory import ChunkKind, CodeChunk, Episode
from src.db.models.pipeline import PipelineRecord
from src.db.models.project import GitProviderKind, Project, ProjectRepo
from src.db.models.task import Task, TaskStatus
from src.db.models.tool import ToolKind, ToolRecord
from src.db.models.user_credential import UserOAuthCredential

__all__ = [
    "AgentRecord",
    "ChunkKind",
    "CodeChunk",
    "Episode",
    "GitProviderKind",
    "PipelineRecord",
    "Project",
    "ProjectRepo",
    "Task",
    "TaskStatus",
    "ToolKind",
    "ToolRecord",
    "UserOAuthCredential",
]
