"""AWS MCP server configuration.

Returns a ``McpHttpServerConfig``-compatible dict that points at the
backend SigV4 proxy endpoint (``/api/v1/mcp/aws``). The proxy signs each
MCP request with the user's IAM credentials and forwards it to the
managed AWS MCP Preview server.

The AWS MCP Preview server requires SigV4 authentication for every
request — a static ``Authorization: Bearer`` header is not supported. The
backend proxy handles signing so the agent can use standard HTTP transport.

Credential transport
--------------------
Instead of passing raw IAM keys as HTTP headers (which can be mangled by
some HTTP clients), we embed them in a short-lived HS256 JWT signed with
``jwt_secret``.  The proxy verifies the signature and extracts the keys
before signing.
"""

from __future__ import annotations

import json
import time
from typing import Any

from joserfc import jwt
from joserfc.jwk import OctKey

from src.config.settings import get_settings


def aws_mcp_server(token: str, raw_metadata: dict[str, Any]) -> dict[str, object]:
    """Return an HTTP MCP server config pointing at the backend SigV4 proxy.

    The user's AWS IAM credentials are packed into a short-lived HS256 JWT
    and passed as ``X-AWS-Credentials``.  The proxy verifies and unpacks
    them before SigV4-signing the forwarded request.

    Args:
        token: JSON-encoded string ``{"access_key_id": "AKIA...",
            "secret_access_key": "..."}``.  Stored as the encrypted payload
            of a ``BEARER`` credential with ``metadata_json["provider"] == "aws"``.
        raw_metadata: Credential metadata. Must contain ``"provider": "aws"``
            and may contain ``"region"`` (defaults to ``"us-east-1"``).

    Returns:
        A ``McpHttpServerConfig`` dict ready for use in ``ClaudeAgentOptions``.
    """
    creds: dict[str, str] = json.loads(token)
    region: str = raw_metadata.get("region", "us-east-1")
    settings = get_settings()

    now = int(time.time())
    key = OctKey.import_key(settings.jwt_secret.get_secret_value().encode())
    credentials_jwt = jwt.encode(
        {"alg": "HS256"},
        {
            "iat": now,
            "exp": now + 3600,
            "access_key_id": creds["access_key_id"],
            "secret_access_key": creds["secret_access_key"],
            "region": region,
        },
        key,
    )

    return {
        "type": "http",
        "url": f"{settings.aws_mcp_proxy_base_url}/api/v1/mcp/aws",
        "headers": {
            "X-AWS-Credentials": credentials_jwt,
        },
    }
