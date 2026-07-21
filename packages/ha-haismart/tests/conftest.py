"""Test config for the HA integration.

These tests need Home Assistant + pytest-homeassistant-custom-component. When those are not
installed (e.g. the library-only CI job), skip the HA test files cleanly rather than erroring.

No sockets are opened: the uSS read cycle (`async_read_status`) and the key-free version probe
(`probe_localkey_version`) are mocked at the point of use, fed with synthetic 127-byte
full-status reports built by `make_status_frame` (same offsets `parse_full_status` decodes).
"""
from __future__ import annotations

try:
    import pytest_homeassistant_custom_component  # noqa: F401

    pytest_plugins = ["pytest_homeassistant_custom_component"]
    _HA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HA_AVAILABLE = False
    collect_ignore = ["test_config_flow.py", "test_init.py"]


def make_status_frame(
    *,
    power: bool = True,
    target_temp: int = 24,
    indoor_temp: float = 26.5,
    outdoor_temp: int = 33,
    mode_code: int = 1,  # STD operationMode (1 = cool on AAC1UKZ01)
    fan_code: int = 5,  # STD windSpeed (5 = auto)
    swing: bool = True,
) -> bytes:
    """Build a synthetic AAC1UKZ01 127-byte full-status report (offsets per uss.py)."""
    frame = bytearray(127)
    frame[2:4] = b"\x27\x15"
    frame[92] = target_temp - 16
    frame[93] = 0x08 if swing else 0x00
    frame[94] = (mode_code << 5) | fan_code
    frame[97] = 1 if power else 0
    frame[104] = int(indoor_temp * 2)
    frame[106] = outdoor_temp + 64
    return bytes(frame)


if _HA_AVAILABLE:
    from unittest.mock import DEFAULT, AsyncMock, patch

    import pytest

    LOCALKEY_VERSION = 4

    @pytest.fixture(autouse=True)
    def _enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001, ANN201
        yield

    @pytest.fixture
    def mock_uss():
        """Patch the uSS entrypoints in both modules that import them.

        Yields a namespace whose `read` (AsyncMock) and `probe` (MagicMock) drive what the
        AC "sends"; `send` (AsyncMock) captures control (grSetDAC) ops so control tests assert the
        exact frame without opening a socket. Tests mutate return_value/side_effect as needed.
        """
        frame = make_status_frame()
        read = AsyncMock(return_value=[frame])

        # ``async_send_op`` is called with a ``build_frame`` callback (single-session
        # read-modify-write): the real function feeds it the AC's post-handshake push; here we
        # simulate that push with ``send.baseline`` (defaults to the current status frame; tests may
        # override it) so the seeding logic runs, and stash the built grSetDAC frame on
        # ``send.last_frame`` for assertions. Returning DEFAULT makes the mock use
        # ``send.return_value`` as the op reply (tests still set that).
        def _send_side_effect(*args, **kwargs):
            build = kwargs.get("build_frame")
            if build is not None:
                send.last_frame = build(send.baseline)
            elif len(args) >= 4 and args[3] is not None:
                send.last_frame = args[3]
            return DEFAULT

        send = AsyncMock(side_effect=_send_side_effect, return_value=[])
        send.baseline = frame
        send.last_frame = None
        with (
            patch(
                "custom_components.haismart.coordinator.async_read_status", read
            ),
            patch(
                "custom_components.haismart.config_flow.async_read_status", read
            ),
            patch(
                "custom_components.haismart.coordinator.async_send_op", send
            ),
            patch(
                "custom_components.haismart.coordinator.probe_localkey_version",
                return_value=LOCALKEY_VERSION,
            ) as probe,
            patch(
                "custom_components.haismart.config_flow.probe_localkey_version",
                probe,
            ),
        ):
            yield type(
                "MockUss", (), {"read": read, "send": send, "probe": probe, "frame": frame}
            )
