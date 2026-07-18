"""Smoke tests for the extracted tapestry-client package."""

from __future__ import annotations

import httpx
import pytest

from tapestry_client import TapestryClient
from tapestry_client.exceptions import TapestryClientError


def test_client_requires_base_url_or_transport() -> None:
    with pytest.raises(ValueError, match="Either base_url or transport"):
        TapestryClient(service_token="tap_service_test")


def test_client_uses_provided_transport() -> None:
    transport = httpx.AsyncClient(base_url="http://stub-tapestry")
    client = TapestryClient(service_token="tap_service_test", transport=transport)
    assert client.service_token == "tap_service_test"


def test_client_error_is_exception() -> None:
    assert issubclass(TapestryClientError, Exception)
