from __future__ import annotations

from pydantic import BaseModel


class AgentRead(BaseModel):
    id: str
    name: str
    description: str


class AgentsList(BaseModel):
    items: list[AgentRead]
