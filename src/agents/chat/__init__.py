"""Long-lived chat session for a single task.

The session keeps one ``ClaudeSDKClient`` open across many user turns.
After the agent finishes a turn the session waits on the task's Redis
input queue for the next user message and feeds it back into the same
client, preserving conversation context (and the Anthropic prefix cache)
across turns.
"""

from src.agents.chat.session import SDKChatSession, SessionEndReason

__all__ = ["SDKChatSession", "SessionEndReason"]
