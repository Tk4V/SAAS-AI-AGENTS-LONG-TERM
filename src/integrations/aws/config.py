"""AWS provider configuration.

AWS uses IAM credentials (SigV4) rather than OAuth. The MCP factory
points the agent at our backend proxy, which handles per-request SigV4
signing and forwarding to the managed AWS MCP Preview server.

Users connect by storing a BEARER credential with:
  - ``token``: JSON-encoded ``{"access_key_id": "...", "secret_access_key": "..."}``
  - ``metadata_json``: ``{"provider": "aws", "region": "us-east-1"}``
"""

from __future__ import annotations

from src.agent_tools.mcp.aws import aws_mcp_server
from src.integrations._shared.config import OAuthProviderConfig
from src.integrations._shared.kinds import IntegrationCategory, IntegrationKind

AWS = OAuthProviderConfig(
    kind=IntegrationKind.AWS,
    category=IntegrationCategory.CLOUD,
    display_name="AWS",
    # AWS does not use OAuth — these fields are required by the dataclass
    # but are never read because the OAuth flow is not used for this provider.
    client_id_setting="",
    client_secret_setting="",
    mcp_factory=aws_mcp_server,
)
