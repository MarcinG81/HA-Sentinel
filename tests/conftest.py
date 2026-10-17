"""Shared fixtures."""

import pytest


@pytest.fixture(autouse=True)
def sentinel_test_env(request, tmp_path):
    """Order matters: HA requires the recorder fixture before hass is created."""
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    if "hass" in request.fixturenames:
        # Allow HA to load custom_components/ha_sentinel in integration tests.
        request.getfixturevalue("enable_custom_integrations")
        # Every HA test gets its own config dir so sentinel.db never leaks between tests.
        request.getfixturevalue("hass").config.config_dir = str(tmp_path)
    yield
