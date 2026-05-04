"""SigV4 proxy for the AWS MCP Preview server.

Receives MCP calls from the Claude Agent SDK, signs them with SigV4 using
IAM credentials from the ``X-AWS-Credentials`` JWT, and forwards to AWS.

The proxy owns the AWS session independently of the SDK: on initialize it
stores the session ID and init payload keyed by access_key_id:region. On
session errors it transparently re-initialises and retries (AWS sessions
don't survive concurrent requests and the SDK won't re-init on 404).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from dataclasses import dataclass, field

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

# ---------------------------------------------------------------------------
# Header filter sets
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Session-error detection
# ---------------------------------------------------------------------------

# "malformed json-rpc" is intentionally excluded: AWS returns that string
# both for genuinely malformed requests AND when a session is dead after
# concurrent access.  Including it caused false-positives that prevented
# legitimate error propagation.
_SESSION_ERROR_KEYWORDS = (
    "sessionid not found",
    "session not found",
    "has expired",
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


# ---------------------------------------------------------------------------
# Proxy-level session store
# ---------------------------------------------------------------------------


@dataclass
class _ProxySession:
    aws_session_id: str
    init_payload: bytes


# key: f"{access_key_id}:{region}"
_sessions: dict[str, _ProxySession] = {}
_session_locks: dict[str, asyncio.Lock] = {}


def _session_key(access_key_id: str, region: str) -> str:
    return f"{access_key_id}:{region}"


def _get_session_lock(key: str) -> asyncio.Lock:
    # asyncio is single-threaded; no race on dict access here.
    if key not in _session_locks:
        _session_locks[key] = asyncio.Lock()
    return _session_locks[key]


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Core forwarding helper
# ---------------------------------------------------------------------------


async def _forward_to_aws(
    method: str,
    url: str,
    body: bytes,
    signing_headers: dict[str, str],
    extra_headers: dict[str, str],
    aws_session_id: str | None,
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> tuple[int, dict[str, str], bytes]:
    """Sign and forward one request to the AWS MCP endpoint.

    ``signing_headers`` must NOT contain ``mcp-session-id``; this function
    injects it (if provided) so that the session ID is always part of the
    signed canonical request.

    Returns ``(status_code, response_headers_dict, body_bytes)``.
    """
    outgoing_sign = dict(signing_headers)
    if aws_session_id:
        outgoing_sign["mcp-session-id"] = aws_session_id

    aws_request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers=outgoing_sign,
    )
    SigV4Auth(
        Credentials(access_key_id, secret_access_key),
        "aws-mcp",
        region,
    ).add_auth(aws_request)

    outgoing_headers = dict(aws_request.headers)
    outgoing_headers.update(extra_headers)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.request(
            method=method,
            url=url,
            headers=outgoing_headers,
            content=body,
        )
        raw = await resp.aread()

    return resp.status_code, dict(resp.headers), raw


# ---------------------------------------------------------------------------
# Re-initialisation helper
# ---------------------------------------------------------------------------


async def _reinitialize_session(
    skey: str,
    url: str,
    signing_headers: dict[str, str],
    extra_headers: dict[str, str],
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> str | None:
    """Re-send the stored initialize payload to AWS and update the session store.

    Returns the new AWS session ID on success, or ``None`` on failure.
    Must be called while holding ``_get_session_lock(skey)``.
    """
    session = _sessions.get(skey)
    if session is None:
        logger.warning("aws_proxy.reinit: no stored init payload for %s", skey)
        return None

    status, resp_headers, _ = await _forward_to_aws(
        method="POST",
        url=url,
        body=session.init_payload,
        signing_headers=signing_headers,
        extra_headers=extra_headers,
        aws_session_id=None,  # must be absent on initialize
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
    )

    new_session_id = resp_headers.get("mcp-session-id")
    if status >= 400 or not new_session_id:
        logger.warning(
            "aws_proxy.reinit_failed: status=%d session_id=%s",
            status,
            new_session_id,
        )
        return None

    _sessions[skey] = _ProxySession(
        aws_session_id=new_session_id,
        init_payload=session.init_payload,
    )
    logger.warning("aws_proxy.session_reinit: new_session=%s", new_session_id)
    return new_session_id


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.api_route("/mcp/aws", methods=["GET", "POST", "DELETE"])
async def aws_mcp_proxy(request: Request) -> Response:
    """Sign and forward MCP requests to the AWS MCP Preview server."""
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
        access_key_id,
        len(secret_access_key),
        region,
    )

    body = await request.body()
    aws_url = f"https://aws-mcp.{region}.api.aws/mcp"
    skey = _session_key(access_key_id, region)

    # ------------------------------------------------------------------
    # Detect method to decide session handling strategy
    # ------------------------------------------------------------------
    rpc_method: str | None = None
    try:
        rpc_method = _json.loads(body).get("method") if body else None
    except Exception:
        pass

    is_initialize = rpc_method == "initialize"

    # ------------------------------------------------------------------
    # Build header buckets (without mcp-session-id — handled separately)
    # ------------------------------------------------------------------
    signing_headers: dict[str, str] = {}
    extra_headers: dict[str, str] = {}

    for k, v in request.headers.items():
        key = k.lower()
        if key in ("x-aws-credentials", "mcp-session-id") or key in _STRIPPED_REQUEST_HEADERS:
            continue
        if key in _UNSIGNED_FORWARD_HEADERS:
            extra_headers[k] = v
        else:
            signing_headers[k] = v

    # ------------------------------------------------------------------
    # Determine which AWS session ID to use
    # ------------------------------------------------------------------
    if is_initialize:
        # No session ID on initialize — AWS creates a fresh session.
        aws_session_id: str | None = None
    else:
        stored = _sessions.get(skey)
        if stored:
            aws_session_id = stored.aws_session_id
        else:
            # Fallback: use whatever the SDK sent (handles edge cases where
            # the server restarted and lost the in-memory store).
            aws_session_id = request.headers.get("mcp-session-id") or None

    # ------------------------------------------------------------------
    # Forward to AWS
    # ------------------------------------------------------------------
    status_code, upstream_headers, raw = await _forward_to_aws(
        method=request.method,
        url=aws_url,
        body=body,
        signing_headers=signing_headers,
        extra_headers=extra_headers,
        aws_session_id=aws_session_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
    )

    logger.warning(
        "aws_proxy.upstream: status=%d content-type=%s",
        status_code,
        upstream_headers.get("content-type", "?"),
    )

    # ------------------------------------------------------------------
    # On initialize success: store session
    # ------------------------------------------------------------------
    if is_initialize and status_code < 400:
        new_session_id = upstream_headers.get("mcp-session-id")
        if new_session_id:
            _sessions[skey] = _ProxySession(
                aws_session_id=new_session_id,
                init_payload=body,
            )
            logger.warning("aws_proxy.session_stored: session=%s", new_session_id)

    # ------------------------------------------------------------------
    # Session error handling with transparent re-initialization
    # ------------------------------------------------------------------
    content_type = upstream_headers.get("content-type", "")
    if "application/json" in content_type and not is_initialize:
        if _is_session_error(raw, upstream_headers):
            lock = _get_session_lock(skey)
            async with lock:
                # Another concurrent request may have already re-initialized.
                current = _sessions.get(skey)
                if current and current.aws_session_id != aws_session_id:
                    # Use the already-refreshed session — just retry.
                    retry_session_id = current.aws_session_id
                    logger.warning(
                        "aws_proxy.session_reuse_after_reinit: session=%s", retry_session_id
                    )
                else:
                    retry_session_id = await _reinitialize_session(
                        skey=skey,
                        url=aws_url,
                        signing_headers=signing_headers,
                        extra_headers=extra_headers,
                        access_key_id=access_key_id,
                        secret_access_key=secret_access_key,
                        region=region,
                    )

            if retry_session_id:
                status_code, upstream_headers, raw = await _forward_to_aws(
                    method=request.method,
                    url=aws_url,
                    body=body,
                    signing_headers=signing_headers,
                    extra_headers=extra_headers,
                    aws_session_id=retry_session_id,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    region=region,
                )
                logger.warning(
                    "aws_proxy.retry_after_reinit: status=%d", status_code
                )
            else:
                # Re-init failed; fall through and return the error to the SDK.
                logger.warning("aws_proxy.reinit_gave_up: returning error to SDK")

    # ------------------------------------------------------------------
    # Build and return response
    # ------------------------------------------------------------------
    response_headers = {
        k: v
        for k, v in upstream_headers.items()
        if k.lower() not in _STRIPPED_RESPONSE_HEADERS
    }

    return Response(content=raw, status_code=status_code, headers=response_headers)
