"""Home Assistant glue tests (pytest-homeassistant-custom-component)."""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "custom_components", "zero_home")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest_plugins = ["pytest_homeassistant_custom_component"]

if sys.platform == "win32":
    # Windows event loops need a loopback socketpair, which pytest-socket blocks.
    # These tests only use 127.0.0.1 (fake relay) and aioclient_mock (Zero Admin).
    import pytest_socket

    pytest_socket.disable_socket = lambda *a, **k: None
    pytest_socket.socket_allow_hosts = lambda *a, **k: None
    # aiodns needs a SelectorEventLoop on Windows; HA's test loop is a Proactor one.
    import aiohttp.resolver
    import homeassistant.helpers.aiohttp_client as _hac

    _hac.AsyncResolver = aiohttp.resolver.ThreadedResolver
    import aiohttp.connector

    aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
