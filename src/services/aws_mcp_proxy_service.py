from __future__ import annotations

import asyncio
import json as _json
import logging
from dataclasses import dataclass

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from src.config.settings import get_settings

logger = logging.getLogger(__name__)


STRIPPED_REQUEST_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "authorization",
        "accept-encoding",
    }
)

UNSIGNED_FORWARD_HEADERS = frozenset({"accept", "user-agent"})

STRIPPED_RESPONSE_HEADERS = frozenset(
    {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "content-encoding",
    }
)

_SESSION_ERROR_KEYWORDS = (
    "sessionid not found",
    "session not found",
    "has expired",
    "sessionid",
)


def is_session_error(body: bytes, headers: dict[str, str]) -> bool:
    error_type = headers.get("x-amzn-errortype", "").lower()
    if "session" in error_type:
        return True
    try:
        msg = _json.loads(body).get("error", {}).get("message", "").lower()
        return any(kw in msg for kw in _SESSION_ERROR_KEYWORDS)
    except Exception:
        return False


@dataclass
class _ProxySession:
    aws_session_id: str
    init_payload: bytes


_sessions: dict[str, _ProxySession] = {}
_session_locks: dict[str, asyncio.Lock] = {}


def _session_key(access_key_id: str, region: str) -> str:
    return f"{access_key_id}:{region}"


def _get_session_lock(key: str) -> asyncio.Lock:
    if key not in _session_locks:
        _session_locks[key] = asyncio.Lock()
    return _session_locks[key]


def decode_credentials(credentials_jwt: str) -> tuple[str, str, str]:
    """Decode the X-AWS-Credentials JWT. Returns (access_key_id, secret, region)."""
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


async def forward_to_aws(
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
    """SigV4-sign and forward one request. Returns (status, headers, body)."""
    outgoing_sign = dict(signing_headers)
    if aws_session_id:
        outgoing_sign["mcp-session-id"] = aws_session_id

    aws_request = AWSRequest(method=method, url=url, data=body, headers=outgoing_sign)
    SigV4Auth(Credentials(access_key_id, secret_access_key), "aws-mcp", region).add_auth(
        aws_request
    )

    outgoing_headers = dict(aws_request.headers)
    outgoing_headers.update(extra_headers)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.request(method=method, url=url, headers=outgoing_headers, content=body)
        raw = await resp.aread()

    return resp.status_code, dict(resp.headers), raw


async def handle_proxy_request(
    http_method: str,
    request_headers: dict[str, str],
    body: bytes,
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> tuple[int, dict[str, str], bytes]:
    """Main entry point: route, sign, session-manage, and return (status, headers, body)."""
    aws_url = f"https://aws-mcp.{region}.api.aws/mcp"
    skey = _session_key(access_key_id, region)

    try:
        rpc_method = _json.loads(body).get("method") if body else None
    except Exception:
        rpc_method = None

    is_initialize = rpc_method == "initialize"

    signing_headers: dict[str, str] = {}
    extra_headers: dict[str, str] = {}

    for k, v in request_headers.items():
        key = k.lower()
        if key in ("x-aws-credentials", "mcp-session-id") or key in STRIPPED_REQUEST_HEADERS:
            continue
        if key in UNSIGNED_FORWARD_HEADERS:
            extra_headers[k] = v
        else:
            signing_headers[k] = v

    if is_initialize:
        aws_session_id: str | None = None
    else:
        stored = _sessions.get(skey)
        aws_session_id = stored.aws_session_id if stored else (
            request_headers.get("mcp-session-id") or None
        )

    status_code, upstream_headers, raw = await forward_to_aws(
        method=http_method,
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

    if is_initialize and status_code < 400:
        new_session_id = upstream_headers.get("mcp-session-id")
        if new_session_id:
            _sessions[skey] = _ProxySession(aws_session_id=new_session_id, init_payload=body)
            logger.warning("aws_proxy.session_stored: session=%s", new_session_id)

    content_type = upstream_headers.get("content-type", "")
    if "application/json" in content_type and not is_initialize:
        if is_session_error(raw, upstream_headers):
            retry_session_id = await _handle_session_error(
                skey=skey,
                aws_url=aws_url,
                signing_headers=signing_headers,
                extra_headers=extra_headers,
                aws_session_id=aws_session_id,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                region=region,
            )
            if retry_session_id:
                status_code, upstream_headers, raw = await forward_to_aws(
                    method=http_method,
                    url=aws_url,
                    body=body,
                    signing_headers=signing_headers,
                    extra_headers=extra_headers,
                    aws_session_id=retry_session_id,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    region=region,
                )
                logger.warning("aws_proxy.retry_after_reinit: status=%d", status_code)
            else:
                logger.warning("aws_proxy.reinit_gave_up: returning error to SDK")

    response_headers = {
        k: v
        for k, v in upstream_headers.items()
        if k.lower() not in STRIPPED_RESPONSE_HEADERS
    }

    return status_code, response_headers, raw


async def _handle_session_error(
    skey: str,
    aws_url: str,
    signing_headers: dict[str, str],
    extra_headers: dict[str, str],
    aws_session_id: str | None,
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> str | None:
    lock = _get_session_lock(skey)
    async with lock:
        current = _sessions.get(skey)
        if current and current.aws_session_id != aws_session_id:
            logger.warning(
                "aws_proxy.session_reuse_after_reinit: session=%s", current.aws_session_id
            )
            return current.aws_session_id
        return await _reinitialize_session(
            skey=skey,
            url=aws_url,
            signing_headers=signing_headers,
            extra_headers=extra_headers,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=region,
        )


async def _reinitialize_session(
    skey: str,
    url: str,
    signing_headers: dict[str, str],
    extra_headers: dict[str, str],
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> str | None:
    session = _sessions.get(skey)
    if session is None:
        logger.warning("aws_proxy.reinit: no stored init payload for %s", skey)
        return None

    status, resp_headers, _ = await forward_to_aws(
        method="POST",
        url=url,
        body=session.init_payload,
        signing_headers=signing_headers,
        extra_headers=extra_headers,
        aws_session_id=None,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
    )

    new_session_id = resp_headers.get("mcp-session-id")
    if status >= 400 or not new_session_id:
        logger.warning("aws_proxy.reinit_failed: status=%d session_id=%s", status, new_session_id)
        return None

    _sessions[skey] = _ProxySession(aws_session_id=new_session_id, init_payload=session.init_payload)
    logger.warning("aws_proxy.session_reinit: new_session=%s", new_session_id)
    return new_session_id
