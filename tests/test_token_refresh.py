"""Token refresh rotation."""

from unittest.mock import MagicMock

import pytest

from custom_components.lidl_plus.auth import refresh_tokens
from custom_components.lidl_plus.exceptions import LidlPlusAuthError


class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


async def test_refresh_persists_rotated_refresh_token():
    session = MagicMock()
    session.post = MagicMock(
        return_value=_Resp(
            200,
            {"access_token": "new-access", "refresh_token": "rotated-refresh"},
        )
    )

    tokens = await refresh_tokens(session, "old-refresh")

    assert tokens["access_token"] == "new-access"
    assert tokens["refresh_token"] == "rotated-refresh"


async def test_refresh_keeps_old_token_when_server_omits_it():
    session = MagicMock()
    session.post = MagicMock(return_value=_Resp(200, {"access_token": "new-access"}))

    tokens = await refresh_tokens(session, "old-refresh")

    assert tokens["refresh_token"] == "old-refresh"


async def test_refresh_rejects_error_status():
    session = MagicMock()
    session.post = MagicMock(return_value=_Resp(400, {"error": "invalid_grant"}))

    with pytest.raises(LidlPlusAuthError):
        await refresh_tokens(session, "old-refresh")
