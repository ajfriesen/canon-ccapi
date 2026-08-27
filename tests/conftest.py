"""Shared fixtures for the Canon CCAPI integration tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ for every test."""
    yield
