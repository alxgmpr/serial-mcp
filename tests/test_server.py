from types import SimpleNamespace

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


def test_server_instructions_describe_session_cleanup_without_forcing_per_call_close():
    """Global guidance should require cleanup without breaking multi-step sessions."""
    from serial_mcp.server import mcp

    instructions = mcp._mcp_server.instructions
    assert "Do not report completion until serial_close succeeds" in instructions
    assert "Never hold a port open between interactions" not in instructions


# ── Session lifecycle metadata tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_serial_open_returns_cleanup_metadata(mock_serial):
    """Opening a session should clearly identify the required cleanup action."""
    from serial_mcp.server import serial_open

    class FakeCtx:
        async def elicit(self, *args, **kwargs):
            raise NotImplementedError

    result = await serial_open(FakeCtx(), port="/dev/ttyTEST", inactivity_timeout=900)
    try:
        assert result["cleanup_required"] is True
        assert result["cleanup_tool"] == "serial_close"
        assert result["last_activity_at"] > 0
        assert result["auto_close_at"] == pytest.approx(result["last_activity_at"] + 900)
        assert "last_activity_at_iso" not in result
        assert "auto_close_at_iso" not in result
    finally:
        if session := _sessions.get("/dev/ttyTEST"):
            session.close()


@pytest.mark.asyncio
async def test_serial_status_returns_exact_auto_close_deadline(mock_serial):
    """Status should expose the deadline derived from the latest activity."""
    from serial_mcp.server import serial_status
    from serial_mcp.session import SerialSession

    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    session._last_activity = 1_700_000_000.0
    _sessions[session.port] = session
    _session_timeouts[session.port] = 900
    try:
        result = await serial_status(session.port)
        assert result["cleanup_required"] is True
        assert result["last_activity_at"] == 1_700_000_000.0
        assert result["auto_close_at"] == 1_700_000_900.0
        assert "last_activity_at_iso" not in result
        assert "auto_close_at_iso" not in result
    finally:
        session.close()


@pytest.mark.asyncio
async def test_serial_list_sessions_includes_lifecycle_metadata(mock_serial):
    """Session listings should expose cleanup and deadline information."""
    from serial_mcp.server import serial_list_sessions
    from serial_mcp.session import SerialSession

    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    session._last_activity = 1_700_000_000.0
    _sessions[session.port] = session
    _session_timeouts[session.port] = 900
    try:
        result = await serial_list_sessions()
        listed = result["sessions"][0]
        assert listed["cleanup_required"] is True
        assert listed["cleanup_tool"] == "serial_close"
        assert listed["auto_close_at"] == 1_700_000_900.0
        assert "auto_close_at_iso" not in listed
    finally:
        session.close()


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


@pytest.mark.asyncio
async def test_serial_open_uses_elicitation_schema_for_port_choice(mock_serial, monkeypatch):
    """Should offer multiple detected ports through a schema-backed elicitation."""
    from serial_mcp.server import serial_open

    monkeypatch.setattr(
        "serial_mcp.server.list_ports.comports",
        lambda: [SimpleNamespace(device="/dev/ttyA"), SimpleNamespace(device="/dev/ttyB")],
    )

    class FakeCtx:
        async def elicit(self, message, schema):
            assert "Multiple serial ports" in message
            return SimpleNamespace(action="accept", data=schema(selection="/dev/ttyB"))

    result = await serial_open(FakeCtx())
    try:
        assert result["session_id"] == "/dev/ttyB"
    finally:
        if session := _sessions.get("/dev/ttyB"):
            session.close()


@pytest.mark.asyncio
async def test_serial_detect_baud_uses_elicitation_schema(monkeypatch):
    """Should accept a non-default baud choice from schema-backed elicitation."""
    from serial_mcp.server import serial_detect_baud

    monkeypatch.setattr("serial_mcp.server._COMMON_BAUD_RATES", [115200, 9600])
    monkeypatch.setattr(
        "serial_mcp.server._test_baud_rate",
        lambda port, baud, probe: {
            "baud_rate": baud,
            "readable_ratio": 0.9 if baud == 115200 else 0.8,
            "bytes_received": 4,
            "sample": "test",
        },
    )

    class FakeCtx:
        async def report_progress(self, progress, total):
            pass

        async def elicit(self, message, schema):
            assert "Baud rate detection complete" in message
            return SimpleNamespace(action="accept", data=schema(selection="9600 (80% readable)"))

    result = await serial_detect_baud(FakeCtx(), "/dev/ttyTEST")
    assert result["recommended"] == 9600


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
