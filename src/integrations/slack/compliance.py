"""Slack-specific Authlib compliance fix.

Slack returns ``200 OK`` with ``{"ok": false, "error": "..."}`` when the
token exchange fails, instead of the RFC 6749 ``400`` with ``error``.
Authlib treats the 200 as success and hands back the bogus body. The
compliance hook flips the response into a real error so callers see the
failure instead of trying to use an empty token.
"""

from __future__ import annotations

import json
from typing import Any


def install_slack_compliance(client: Any) -> None:
    def _flag_ok_false(resp: Any) -> Any:
        try:
            body = json.loads(resp.text or "{}")
        except json.JSONDecodeError:
            return resp
        if isinstance(body, dict) and body.get("ok") is False:
            resp.status_code = 400
        return resp

    client.register_compliance_hook("access_token_response", _flag_ok_false)
    client.register_compliance_hook("refresh_token_response", _flag_ok_false)
