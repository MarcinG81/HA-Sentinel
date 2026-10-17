"""Shared fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow HA to load custom_components/ha_sentinel in integration tests."""
    yield


@pytest.fixture(autouse=True)
def isolated_config_dir(request, tmp_path):
    """Give every HA test its own config dir so sentinel.db never leaks between tests."""
    if "hass" in request.fixturenames:
        request.getfixturevalue("hass").config.config_dir = str(tmp_path)
    yield
