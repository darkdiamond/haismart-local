"""Test config. `pythonpath=["src"]` (pyproject) puts the library on the path.

If the venv also has the HA integration's test harness installed (``pytest-homeassistant-custom-component``,
which pulls in ``pytest-socket`` and disables sockets for every test), re-enable loopback here so the
documented single-venv workflow — all three suites in one ``.venv`` — keeps working.
"""
from __future__ import annotations

try:
    import pytest
    from pytest_socket import enable_socket

    @pytest.fixture(autouse=True)
    def _allow_loopback_sockets():  # noqa: ANN202
        # runs after pytest-socket's setup hook, so it wins for the test body
        enable_socket()
        yield
except ImportError:  # pytest-socket not installed — nothing to undo
    pass
