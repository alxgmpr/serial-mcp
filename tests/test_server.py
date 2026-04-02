import pytest

from serial_mcp.server import _resolve_session, _sessions


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure sessions dict is empty before/after each test."""
    _sessions.clear()
    yield
    _sessions.clear()


def test_resolve_session_no_sessions():
    """Should raise when no sessions are open."""
    with pytest.raises(RuntimeError, match="No sessions open"):
        _resolve_session()


def test_resolve_session_single(mock_serial):
    """Should auto-select when only one session is open."""
    from serial_mcp.session import SerialSession

    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    _sessions["/dev/ttyTEST"] = session
    try:
        result = _resolve_session()
        assert result is session
    finally:
        session.close()


def test_resolve_session_by_id(mock_serial):
    """Should return the correct session by ID."""
    from serial_mcp.session import SerialSession

    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    _sessions["/dev/ttyTEST"] = session
    try:
        result = _resolve_session("/dev/ttyTEST")
        assert result is session
    finally:
        session.close()


def test_resolve_session_invalid_id():
    """Should raise for an unknown session ID."""
    with pytest.raises(RuntimeError, match="No session open"):
        _resolve_session("/dev/ttyNONE")
