"""Re-export every ORM model so Alembic's autogenerate sees the full metadata.

`env.py` imports this module with `from src.db import models` and relies on
side-effect registration. Adding a new model means appending it here.
"""

from src.db.models.agent import (
    Agent,
    AgentSubagent,
    AgentSubagentMcp,
    SubagentSystemTool,
    SystemTool,
)
from src.db.models.agent_config import (
    AgentToolConfig,
    MCPServerConfig,
    Subagent,
    SubagentTool,
    UserToolConfig,
)
from src.db.models.credential import Credential, CredentialKind
from src.db.models.credential_event import CredentialEvent, CredentialEventType
from src.db.models.project import ProviderKind, Project, ProjectRepo
from src.db.models.memory_graph import MemoryEdge, MemoryNode
from src.db.models.task import Task, TaskStatus
from src.db.models.task_approval import ApprovalStatus, TaskApproval
from src.db.models.task_message import MessageKind, MessageRole, TaskMessage

__all__ = [
    "Agent",
    "AgentSubagent",
    "AgentSubagentMcp",
    "AgentToolConfig",
    "ApprovalStatus",
    "Credential",
    "CredentialEvent",
    "CredentialEventType",
    "CredentialKind",
    "MCPServerConfig",
    "MemoryEdge",
    "MemoryNode",
    "MessageKind",
    "MessageRole",
    "ProviderKind",
    "Project",
    "ProjectRepo",
    "Subagent",
    "SubagentSystemTool",
    "SubagentTool",
    "SystemTool",
    "Task",
    "TaskApproval",
    "TaskMessage",
    "TaskStatus",
    "UserToolConfig",
]
