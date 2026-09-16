"""OAuth redirect parsing for browser login."""

import pytest

from custom_components.lidl_plus.auth import (
    auth_code_from_redirect,
    authorization_url,
    generate_pkce,
)
from custom_components.lidl_plus.exceptions import LidlPlusAuthError


def test_auth_code_from_app_url():
    assert (
        auth_code_from_redirect("com.lidlplus.app://callback?code=abc.def&state=1")
        == "abc.def"
    )


def test_auth_code_from_noisy_copy():
    text = (
        "Failed to launch 'com.lidlplus.app://callback?code=xyz123"
        "&iss=https%3A%2F%2Faccounts.lidl.com'"
    )
    assert auth_code_from_redirect(text) == "xyz123"


def test_auth_code_raw():
    assert auth_code_from_redirect("  onlyTheCode  ") == "onlyTheCode"


def test_auth_code_empty():
    with pytest.raises(LidlPlusAuthError, match="missing_auth_code"):
        auth_code_from_redirect("   ")


def test_authorization_url_contains_pkce_and_country():
    _, challenge = generate_pkce()
    url = authorization_url(
        challenge, country="PL", language="pl-PL", state="st", nonce="nn"
    )
    assert "accounts.lidl.com/connect/authorize" in url
    assert "client_id=LidlPlusNativeClient" in url
    assert "code_challenge_method=S256" in url
    assert "Country=PL" in url
    assert "language=pl-PL" in url
    assert challenge in url
