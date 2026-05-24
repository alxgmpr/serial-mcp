import pytest

from serial_mcp.server import (
    _auto_closed_sessions,
    _normalize_output,
    _resolve_respond,
    _resolve_session,
    _session_timeouts,
    _sessions,
)


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure sessions dict is empty before/after each test."""
    _sessions.clear()
    _session_timeouts.clear()
    _auto_closed_sessions.clear()
    yield
    _sessions.clear()
    _session_timeouts.clear()
    _auto_closed_sessions.clear()


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


# ── Output normalization tests ─────────────────────────────────────


def test_normalize_output_crlf():
    assert _normalize_output("hello\r\nworld\r\n") == "hello\nworld"


def test_normalize_output_cr():
    assert _normalize_output("hello\rworld\r") == "hello\nworld"


def test_normalize_output_mixed():
    assert _normalize_output("line1\r\nline2\rline3\n") == "line1\nline2\nline3"


def test_normalize_output_trailing_whitespace():
    assert _normalize_output("hello   \r\nworld  \r\n") == "hello\nworld"


def test_normalize_output_empty():
    assert _normalize_output("") == ""


def test_normalize_output_no_change():
    assert _normalize_output("clean output") == "clean output"


# ── Auto-close notification tests ──────────────────────────────────


def test_resolve_session_auto_closed_by_id():
    """Should raise with auto-close message for a specific session."""
    _auto_closed_sessions["/dev/ttyTEST"] = "Session on /dev/ttyTEST was automatically closed after 15 minutes."
    with pytest.raises(RuntimeError, match="automatically closed"):
        _resolve_session("/dev/ttyTEST")
    assert "/dev/ttyTEST" not in _auto_closed_sessions


def test_resolve_session_auto_closed_any():
    """Should raise with auto-close message when no session_id specified."""
    _auto_closed_sessions["/dev/ttyTEST"] = "Session on /dev/ttyTEST was automatically closed after 15 minutes."
    with pytest.raises(RuntimeError, match="automatically closed"):
        _resolve_session()
    assert len(_auto_closed_sessions) == 0


# ── Port-busy error tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_serial_open_port_busy(monkeypatch):
    """Should raise clear error when port is busy."""
    import serial

    from serial_mcp.server import serial_open

    monkeypatch.setattr(
        "serial_mcp.server.SerialSession",
        lambda **kw: (_ for _ in ()).throw(serial.SerialException("could not open port: [Errno 16] Resource busy")),
    )
    monkeypatch.setattr("serial_mcp.server._get_process_holding_port", lambda p: None)

    class FakeCtx:
        async def elicit(self, *a, **kw):
            raise NotImplementedError

    with pytest.raises(RuntimeError, match="in use by another process"):
        await serial_open(FakeCtx(), port="/dev/ttyBUSY")


@pytest.mark.asyncio
async def test_serial_open_port_busy_with_pid(monkeypatch):
    """Should include PID and command when process is identified."""
    import serial

    from serial_mcp.server import serial_open

    monkeypatch.setattr(
        "serial_mcp.server.SerialSession",
        lambda **kw: (_ for _ in ()).throw(serial.SerialException("could not open port: Permission denied")),
    )
    monkeypatch.setattr(
        "serial_mcp.server._get_process_holding_port",
        lambda p: {"pid": 1234, "user": "root", "command": "minicom"},
    )

    class FakeCtx:
        async def elicit(self, *a, **kw):
            raise NotImplementedError

    with pytest.raises(RuntimeError, match="PID 1234.*minicom"):
        await serial_open(FakeCtx(), port="/dev/ttyBUSY")


@pytest.mark.asyncio
async def test_serial_open_generic_error(monkeypatch):
    """Non-busy serial errors should get a helpful message."""
    import serial

    from serial_mcp.server import serial_open

    monkeypatch.setattr(
        "serial_mcp.server.SerialSession",
        lambda **kw: (_ for _ in ()).throw(serial.SerialException("port not found")),
    )

    class FakeCtx:
        async def elicit(self, *a, **kw):
            raise NotImplementedError

    with pytest.raises(RuntimeError, match="Could not open port.*port not found"):
        await serial_open(FakeCtx(), port="/dev/ttyNONE")


# ── Triggered response validation tests ──────────────────────────


def test_resolve_respond_text():
    result = _resolve_respond("hello", None, "utf-8")
    assert result == b"hello"


def test_resolve_respond_hex():
    result = _resolve_respond(None, "7F", "utf-8")
    assert result == b"\x7f"


def test_resolve_respond_hex_with_spaces():
    result = _resolve_respond(None, "AA 55 01", "utf-8")
    assert result == b"\xaa\x55\x01"


def test_resolve_respond_none():
    result = _resolve_respond(None, None, "utf-8")
    assert result is None


def test_resolve_respond_empty_string_is_none():
    assert _resolve_respond("", None, "utf-8") is None
    assert _resolve_respond(None, "", "utf-8") is None


def test_resolve_respond_both_raises():
    with pytest.raises(ValueError, match="Cannot set both"):
        _resolve_respond("hello", "7F", "utf-8")


def test_resolve_respond_invalid_hex():
    with pytest.raises(ValueError, match="Invalid respond_hex"):
        _resolve_respond(None, "ZZ", "utf-8")


@pytest.mark.asyncio
async def test_serial_command_respond_without_expect(mock_serial):
    """Should raise ValueError when respond is set without expect."""
    from serial_mcp.server import serial_command
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
        with pytest.raises(ValueError, match="respond/respond_hex requires expect"):
            await serial_command(data="test", respond=" ")
    finally:
        session.close()
