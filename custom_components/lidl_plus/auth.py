"""OAuth2 PKCE login and token refresh for Lidl Plus."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from .const import (
    API_USER_AGENT,
    AUTH_SCOPES,
    AUTH_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    TOKEN_URL,
)
from .exceptions import LidlPlusAuthError, LidlPlusCannotConnect

_LOGGER = logging.getLogger(__name__)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return {}


def _basic_auth_header() -> str:
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode("ascii")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def authorization_url(
    challenge: str,
    *,
    country: str,
    language: str,
    state: str,
    nonce: str,
) -> str:
    """Authorize URL for the Lidl Plus native client."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": AUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "Country": country,
        "language": language,
        "state": state,
        "nonce": nonce,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def auth_code_from_redirect(value: str) -> str:
    """Extract the OAuth code from a com.lidlplus.app:// redirect or a raw code."""
    raw = value.strip().strip('"').strip("'")
    if not raw:
        raise LidlPlusAuthError("missing_auth_code")
    marker = "com.lidlplus.app://"
    app_idx = raw.find(marker)
    if app_idx >= 0:
        raw = raw[app_idx:].split()[0].rstrip(".,;\"'")
    if "://" in raw or "code=" in raw:
        return _code_from_redirect(raw)
    return raw


def _code_from_redirect(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if not query.get("code") and parsed.fragment:
        query = parse_qs(parsed.fragment)
    code = (query.get("code") or [None])[0]
    if not code:
        raise LidlPlusAuthError("missing_auth_code")
    return code


class OAuthLogin:
    """PKCE session for LidlPlusNativeClient — Companion opens the authorize URL."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        country: str,
        language: str,
    ) -> None:
        self._session = session
        self.country = country
        self.language = language
        self.verifier: str | None = None
        self.auth_url: str | None = None
        self.state: str | None = None
        self.nonce: str | None = None
        self._owns_session = False

    @classmethod
    def create(cls, *, country: str, language: str) -> OAuthLogin:
        jar = aiohttp.CookieJar(unsafe=True)
        session = aiohttp.ClientSession(
            cookie_jar=jar,
            headers={
                "User-Agent": API_USER_AGENT,
                "Accept-Language": language,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        login = cls(session, country=country, language=language)
        login._owns_session = True
        return login

    async def close(self) -> None:
        if self._owns_session and not self._session.closed:
            await self._session.close()

    def prepare(self) -> str:
        """Generate PKCE and return the authorize URL (no HTTP yet)."""
        if not self.verifier or not self.auth_url:
            self.verifier, challenge = generate_pkce()
            self.state = _b64url(secrets.token_bytes(16))
            self.nonce = _b64url(secrets.token_bytes(16))
            self.auth_url = authorization_url(
                challenge,
                country=self.country,
                language=self.language,
                state=self.state,
                nonce=self.nonce,
            )
        return self.auth_url

    async def exchange_code(self, code: str, verifier: str) -> dict[str, str]:
        data = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
        }
        return await _token_request(self._session, data)


async def refresh_tokens(
    session: aiohttp.ClientSession, refresh_token: str
) -> dict[str, str]:
    """Refresh; persist the new refresh token — Lidl rotates it every call."""
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "refresh_token": refresh_token,
    }
    return await _token_request(session, data, allow_same_refresh=True)


async def _token_request(
    session: aiohttp.ClientSession,
    data: dict[str, str],
    *,
    allow_same_refresh: bool = False,
) -> dict[str, str]:
    try:
        async with session.post(
            TOKEN_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": _basic_auth_header(),
                "User-Agent": API_USER_AGENT,
            },
        ) as resp:
            payload = await resp.json(content_type=None)
            if resp.status >= 400:
                _LOGGER.debug("Token error %s: %s", resp.status, payload)
                raise LidlPlusAuthError("token_rejected")
    except TimeoutError as err:
        raise LidlPlusCannotConnect("timeout") from err
    except aiohttp.ClientError as err:
        raise LidlPlusCannotConnect(str(err)) from err
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if allow_same_refresh:
        refresh = refresh or data.get("refresh_token")
    if not access or not refresh:
        raise LidlPlusAuthError("token_rejected")
    return {"access_token": access, "refresh_token": refresh}
