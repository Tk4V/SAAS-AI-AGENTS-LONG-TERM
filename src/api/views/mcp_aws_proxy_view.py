"""Backend SigV4 proxy for the AWS MCP Preview server.

The AWS MCP Preview server (https://aws-mcp.{region}.api.aws/mcp) requires
per-request SigV4 signing, which cannot be expressed as a static
``Authorization: Bearer`` header. This proxy:

1. Receives MCP JSON-RPC calls from the Claude Agent SDK (standard HTTP).
2. Reads the user's IAM credentials from the ``X-AWS-Credentials`` JWT set
   by the MCP factory (``src/agent_tools/mcp/aws.py``).
3. Verifies the JWT with the shared ``jwt_secret`` using joserfc.
4. Signs the forwarded request with SigV4 using ``botocore``.
5. Streams the AWS MCP response back to the caller.

Security: the credential JWT is signed (HS256 / jwt_secret) so the proxy
can reject tampered headers. The JWT is transmitted over TLS (HTTPS in
production) and stripped before forwarding to AWS.

SigV4 signing strategy
-----------------------
Only ``content-type`` and MCP session headers (``mcp-session-id``) are
included in the canonical signed-headers list.  Headers like ``accept`` and
``accept-encoding`` are forwarded to AWS but kept *outside* the signed set so
that any in-flight normalisation by the HTTP client cannot invalidate the
signature.  AWS SigV4 allows unsigned forwarded headers; only the headers
listed in ``SignedHeaders`` are verified.
"""

from __future__ import annotations

import json as _json
import logging

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp"])

# Headers we never forward to AWS at all.
_STRIPPED_REQUEST_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "authorization",
        "accept-encoding",
    }
)

# Headers forwarded to AWS but intentionally kept out of the SigV4 signed set.
# Signing them is fragile because the HTTP client may normalise their values.
_UNSIGNED_FORWARD_HEADERS = frozenset(
    {
        "accept",
        "user-agent",
    }
)

_STRIPPED_RESPONSE_HEADERS = frozenset(
    {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "content-encoding",
    }
)

# Keywords that indicate an AWS MCP session has expired or is unknown.
# AWS MCP returns HTTP 200 with a JSON-RPC error body for these cases instead
# of the HTTP 404 that the MCP Streamable HTTP spec prescribes.  By mapping
# these responses to 404 we allow the Claude SDK to re-initialize the session.
_SESSION_ERROR_KEYWORDS = (
    "sessionid not found",
    "session not found",
    "has expired",
    "malformed json-rpc",
    "sessionid",
)


def _is_session_error(body: bytes, headers: dict[str, str]) -> bool:
    """Return True if the AWS MCP response signals a dead/unknown session."""
    error_type = headers.get("x-amzn-errortype", "").lower()
    if "session" in error_type:
        return True
    try:
        msg = _json.loads(body).get("error", {}).get("message", "").lower()
        return any(kw in msg for kw in _SESSION_ERROR_KEYWORDS)
    except Exception:
        return False


def _decode_credentials(credentials_jwt: str) -> tuple[str, str, str]:
    """Decode and verify the ``X-AWS-Credentials`` JWT.

    Returns ``(access_key_id, secret_access_key, region)``.
    Raises ``ValueError`` on any verification failure.
    """
    settings = get_settings()
    key = OctKey.import_key(settings.jwt_secret.get_secret_value().encode())
    try:
        token = jwt.decode(credentials_jwt, key)
        claims = token.claims
    except JoseError as exc:
        raise ValueError(f"Invalid AWS credentials JWT: {exc}") from exc

    access_key_id = claims.get("access_key_id", "")
    secret_access_key = claims.get("secret_access_key", "")
    region = claims.get("region", "us-east-1")

    if not access_key_id or not secret_access_key:
        raise ValueError("AWS credentials JWT is missing access_key_id or secret_access_key.")

    return access_key_id, secret_access_key, region


@router.api_route("/mcp/aws", methods=["GET", "POST", "DELETE"])
async def aws_mcp_proxy(request: Request) -> Response:
    """Sign and forward MCP requests to the AWS MCP Preview server.

    Expects ``X-AWS-Credentials``: a short-lived HS256 JWT (set by the MCP
    factory in ``src/agent_tools/mcp/aws.py``) containing:
      - ``access_key_id``
      - ``secret_access_key``
      - ``region`` (optional, defaults to ``us-east-1``)

    The JWT header is stripped before the request reaches AWS.
    """
    credentials_jwt = request.headers.get("X-AWS-Credentials", "")
    if not credentials_jwt:
        return Response(content="Missing X-AWS-Credentials header.", status_code=401)

    try:
        access_key_id, secret_access_key, region = _decode_credentials(credentials_jwt)
    except ValueError as exc:
        logger.warning("aws_proxy.bad_credentials: %s", exc)
        return Response(content=str(exc), status_code=401)

    logger.warning(
        "aws_proxy: key_id=%s secret_len=%d region=%s",
        access_key_id,  # access key ID is non-sensitive (public half of key pair)
        len(secret_access_key),
        region,
    )

    body = await request.body()
    aws_url = f"https://aws-mcp.{region}.api.aws/mcp"

    # Split incoming headers into two buckets:
    #   signing_headers  — included in the SigV4 canonical request
    #   extra_headers    — forwarded to AWS but not signed
    signing_headers: dict[str, str] = {}
    extra_headers: dict[str, str] = {}

    for k, v in request.headers.items():
        key = k.lower()
        # Drop our custom credential header and all hop-by-hop / auth headers.
        if key == "x-aws-credentials" or key in _STRIPPED_REQUEST_HEADERS:
            continue
        if key in _UNSIGNED_FORWARD_HEADERS:
            extra_headers[k] = v  # forward, but don't sign
        else:
            signing_headers[k] = v  # sign and forward

    # SigV4-sign only the signing_headers subset.
    aws_request = AWSRequest(
        method=request.method,
        url=aws_url,
        data=body,
        headers=signing_headers,
    )
    SigV4Auth(
        Credentials(access_key_id, secret_access_key),
        "aws-mcp",
        region,
    ).add_auth(aws_request)

    # Merge: signed headers (incl. Authorization, x-amz-date, host) + unsigned extras.
    outgoing_headers: dict[str, str] = dict(aws_request.headers)
    outgoing_headers.update(extra_headers)

    # Stream the upstream response so we can capture its real headers (Content-Type,
    # Mcp-Session-Id, etc.) and pass them through unchanged.  The generator yields
    # the (status, headers) tuple first, then raw bytes.  We advance it one step
    # to get the headers before constructing the StreamingResponse, which keeps the
    # httpx client alive for the full response duration via the generator context.
    async def _upstream() -> object:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as _client:
            async with _client.stream(
                method=request.method,
                url=aws_url,
                headers=outgoing_headers,
                content=body,
            ) as _resp:
                yield _resp.status_code, dict(_resp.headers)
                async for chunk in _resp.aiter_bytes():
                    yield chunk

    gen = _upstream()
    status_code, upstream_headers = await gen.__anext__()  # type: ignore[misc]

    logger.warning(
        "aws_proxy.upstream: status=%d content-type=%s",
        status_code,
        upstream_headers.get("content-type", "?"),
    )

    response_headers = {
        k: v
        for k, v in upstream_headers.items()
        if k.lower() not in _STRIPPED_RESPONSE_HEADERS
    }

    # JSON responses are small (single JSON-RPC object).  Buffer them so we can
    # inspect the body for session errors and return 404 when the session has
    # expired — the MCP spec says 404 means "session unknown, please re-init".
    content_type = upstream_headers.get("content-type", "")
    if "application/json" in content_type:
        chunks: list[bytes] = []
        async for chunk in gen:
            chunks.append(chunk)
        raw = b"".join(chunks)

        if _is_session_error(raw, upstream_headers):
            logger.warning("aws_proxy.session_expired: returning 404 to trigger re-init")
            return Response(content=raw, status_code=404, headers=response_headers)

        return Response(content=raw, status_code=status_code, headers=response_headers)

    # SSE / unknown content-type: stream bytes unchanged.
    async def _body() -> object:
        async for chunk in gen:
            yield chunk

    return StreamingResponse(
        _body(),
        status_code=status_code,
        headers=response_headers,
    )
