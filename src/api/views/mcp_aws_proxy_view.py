from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from src.services.aws_mcp_proxy_service import decode_credentials, handle_proxy_request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["MCP Proxies"])


@router.api_route("/mcp/aws", methods=["GET", "POST", "DELETE"])
async def aws_mcp_proxy(request: Request) -> Response:
    credentials_jwt = request.headers.get("X-AWS-Credentials", "")
    if not credentials_jwt:
        return Response(content="Missing X-AWS-Credentials header.", status_code=401)

    try:
        access_key_id, secret_access_key, region = decode_credentials(credentials_jwt)
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
    status_code, response_headers, raw = await handle_proxy_request(
        http_method=request.method,
        request_headers=dict(request.headers),
        body=body,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
    )

    return Response(content=raw, status_code=status_code, headers=response_headers)
