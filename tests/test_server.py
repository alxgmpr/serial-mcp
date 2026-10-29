import errno
import json
import subprocess
import sys
import threading
import time
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

    instructions = mcp._lowlevel_server.instructions
    assert "Do not report completion until serial_close succeeds" in instructions
    assert "serial_execute() opens and closes its own session" in instructions
    assert "Never hold a port open between interactions" not in instructions


def test_server_reports_package_version():
    """MCP initialization should identify serial-mcp, not the SDK version."""
    from importlib.metadata import version

    from serial_mcp.server import mcp

    assert mcp._lowlevel_server.version == version("pyserial-mcp")


def test_server_import_has_no_warnings():
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", "import serial_mcp.server"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_core_profile_exposes_only_the_seven_common_workflow_tools():
    from serial_mcp.server import _apply_tool_profile, mcp

    _apply_tool_profile("core")
    try:
        names = {tool.name for tool in await mcp.list_tools()}
        assert names == {
            "list_serial_ports",
            "serial_open",
            "serial_close",
            "serial_execute",
            "serial_command",
            "serial_detect_baud",
            "serial_status",
        }
    finally:
        _apply_tool_profile("full")


@pytest.mark.asyncio
async def test_tool_descriptions_are_less_than_one_quarter_of_schema_payload():
    from serial_mcp.server import mcp

    tools = await mcp.list_tools()
    schema_bytes = sum(len(json.dumps(tool.model_dump(exclude_none=True))) for tool in tools)
    description_bytes = sum(len(tool.description or "") for tool in tools)
    assert description_bytes < schema_bytes / 4


# ── Session lifecycle metadata tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_serial_open_returns_cleanup_metadata(mock_serial):
    """Opening a session should clearly identify the required cleanup action."""
    from serial_mcp.server import serial_open

    result = await serial_open(port="/dev/ttyTEST", inactivity_timeout=900)
    try:
        assert result["cleanup_required"] is True
        assert result["cleanup_tool"] == "serial_close"
        assert result["last_activity_at"] > 0
        assert result["auto_close_at"] == pytest.approx(result["last_activity_at"] + 900)
        assert isinstance(result["connected_at"], int)
        assert isinstance(result["last_activity_at"], int)
        assert isinstance(result["auto_close_at"], int)
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
        assert isinstance(result["connected_at"], int)
        assert isinstance(result["last_activity_at"], int)
        assert isinstance(result["auto_close_at"], int)
        assert "port" not in result
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


# ── One-shot command tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_serial_execute_runs_command_and_closes_session(mock_serial):
    """One-shot execution should return the response without leaving a session open."""
    from serial_mcp import server

    serial_execute = getattr(server, "serial_execute", None)
    assert serial_execute is not None

    def delayed_response():
        time.sleep(0.05)
        mock_serial.inject_data(b"OK\r\n")

    response_thread = threading.Thread(target=delayed_response)
    response_thread.start()
    result = await serial_execute(
        data="AT",
        port="/dev/ttyTEST",
        baud_rate=9600,
        expect="OK",
        timeout=1.0,
    )
    response_thread.join()

    assert result["data"] == "OK"
    assert result["matched"] == "OK"
    assert result["port"] == "/dev/ttyTEST"
    assert result["closed"] is True
    assert "session_id" not in result
    assert "cleanup_required" not in result
    assert _sessions == {}
    assert mock_serial.is_open is False


@pytest.mark.asyncio
async def test_serial_execute_closes_session_when_command_fails(mock_serial):
    """One-shot execution should release the port when command processing raises."""
    from serial_mcp import server

    serial_execute = getattr(server, "serial_execute", None)
    assert serial_execute is not None

    with pytest.raises(LookupError):
        await serial_execute(
            data="AT",
            port="/dev/ttyTEST",
            encoding="not-an-encoding",
        )

    assert _sessions == {}
    assert mock_serial.is_open is False


def test_serial_execute_description_uses_compact_crlf_text():
    """The tool description should not expand escaped newlines into extra tokens."""
    from serial_mcp import server

    assert "append CRLF" in server.serial_execute.__doc__
    assert "\r" not in server.serial_execute.__doc__
    assert len(server.serial_execute.__doc__) < 550


# ── Output normalization tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_serial_read_limits_output_to_most_recent_bytes(mock_serial):
    from serial_mcp.server import serial_read
    from serial_mcp.session import SerialSession

    session = SerialSession(port="/dev/ttyTEST", baud_rate=115200, data_bits=8, stop_bits=1, parity="none", timeout=1.0)
    _sessions[session.port] = session
    try:
        mock_serial.inject_data(b"boot-older\nboot-newest")
        time.sleep(0.05)
        result = await serial_read(max_output_bytes=11)
        assert result["data"] == "boot-newest"
        assert result["byte_count"] == 22
        assert result["returned_bytes"] == 11
        assert result["omitted_bytes"] == 11
        assert result["truncated"] is True
    finally:
        session.close()


@pytest.mark.asyncio
async def test_serial_command_reports_untruncated_output(mock_serial):
    from serial_mcp.server import serial_command
    from serial_mcp.session import SerialSession

    session = SerialSession(port="/dev/ttyTEST", baud_rate=115200, data_bits=8, stop_bits=1, parity="none", timeout=1.0)
    _sessions[session.port] = session

    def delayed_response():
        time.sleep(0.05)
        mock_serial.inject_data(b"OK")

    response_thread = threading.Thread(target=delayed_response)
    response_thread.start()
    try:
        result = await serial_command(data="AT", expect="OK", timeout=1, max_output_bytes=16)
        assert result["data"] == "OK"
        assert result["returned_bytes"] == 2
        assert result["omitted_bytes"] == 0
        assert result["truncated"] is False
    finally:
        response_thread.join()
        session.close()


@pytest.mark.asyncio
async def test_serial_read_since_limits_output_and_integerizes_time_range(mock_serial):
    from serial_mcp.server import serial_read_since
    from serial_mcp.session import SerialSession

    session = SerialSession(port="/dev/ttyTEST", baud_rate=115200, data_bits=8, stop_bits=1, parity="none", timeout=1.0)
    _sessions[session.port] = session
    try:
        mock_serial.inject_data(b"1234567890")
        time.sleep(0.05)
        result = await serial_read_since(max_output_bytes=4)
        assert result["data"] == "7890"
        assert result["truncated"] is True
        assert result["omitted_bytes"] == 6
        assert isinstance(result["connected_at"], int)
        assert isinstance(result["time_range"]["earliest"], int)
        assert isinstance(result["time_range"]["latest"], int)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_serial_read_hex_limits_raw_bytes(mock_serial):
    from serial_mcp.server import serial_read_hex
    from serial_mcp.session import SerialSession

    session = SerialSession(port="/dev/ttyTEST", baud_rate=115200, data_bits=8, stop_bits=1, parity="none", timeout=1.0)
    _sessions[session.port] = session
    try:
        mock_serial.inject_data(bytes(range(10)))
        time.sleep(0.05)
        result = await serial_read_hex(max_output_bytes=4)
        assert result["hex"] == "06 07 08 09"
        assert result["byte_count"] == 10
        assert result["returned_bytes"] == 4
        assert result["omitted_bytes"] == 6
        assert result["truncated"] is True
    finally:
        session.close()


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
    from serial_mcp.server import serial_open

    monkeypatch.setattr(
        "serial_mcp.server.SerialSession",
        lambda **kw: (_ for _ in ()).throw(OSError(errno.EBUSY, "Serial port is already locked by another process")),
    )
    monkeypatch.setattr("serial_mcp.server._get_process_holding_port", lambda p: None)

    with pytest.raises(RuntimeError, match="in use by another process"):
        await serial_open(port="/dev/ttyBUSY")


@pytest.mark.asyncio
async def test_serial_open_port_busy_with_pid(monkeypatch):
    """Should include PID and command when process is identified."""
    from serial_mcp.server import serial_open

    monkeypatch.setattr(
        "serial_mcp.server.SerialSession",
        lambda **kw: (_ for _ in ()).throw(PermissionError(errno.EACCES, "Permission denied")),
    )
    monkeypatch.setattr(
        "serial_mcp.server._get_process_holding_port",
        lambda p: {"pid": 1234, "user": "root", "command": "minicom"},
    )

    with pytest.raises(RuntimeError, match="PID 1234.*minicom"):
        await serial_open(port="/dev/ttyBUSY")


@pytest.mark.asyncio
async def test_serial_open_generic_error(monkeypatch):
    """Non-busy serial errors should get a helpful message."""
    from serial_mcp.server import serial_open

    monkeypatch.setattr(
        "serial_mcp.server.SerialSession",
        lambda **kw: (_ for _ in ()).throw(FileNotFoundError(errno.ENOENT, "port not found")),
    )

    with pytest.raises(RuntimeError, match="Could not open port.*port not found"):
        await serial_open(port="/dev/ttyNONE")


_TWO_PORTS = [
    SimpleNamespace(device="/dev/ttyA", resolved_device="/dev/ttyA"),
    SimpleNamespace(device="/dev/serial/by-id/b", resolved_device="/dev/ttyB"),
]


def _answer(action, selection=None, seen=None):
    """Client elicitation callback that records the prompt and replies with `selection`."""
    from mcp_types import ElicitResult

    async def callback(context, params):
        if seen is not None:
            seen.append(params)
        return ElicitResult(action=action, content={"selection": selection} if selection else None)

    return callback


async def _call(mode, name, arguments, elicitation_callback=None):
    from mcp.client import Client

    from serial_mcp.server import mcp

    async with Client(mcp, mode=mode, elicitation_callback=elicitation_callback) as client:
        return await client.call_tool(name, arguments)


def _data(result):
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_serial_open_elicits_port_choice(mock_serial, monkeypatch, mode):
    """Should offer multiple detected ports through a schema-backed elicitation."""
    monkeypatch.setattr("serialx.list_serial_ports", lambda: _TWO_PORTS)
    seen = []

    result = await _call(mode, "serial_open", {}, _answer("accept", "/dev/ttyB", seen))
    try:
        assert not result.is_error, result.content
        assert _data(result)["session_id"] == "/dev/ttyB"
        assert "Multiple serial ports" in seen[0].message
        assert seen[0].requested_schema["properties"]["selection"]["enum"] == ["/dev/ttyA", "/dev/ttyB"]
    finally:
        if session := _sessions.get("/dev/ttyB"):
            session.close()


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_serial_open_port_choice_declined(monkeypatch, mode):
    """Declining the port prompt should return a cancellation, not open a port."""
    monkeypatch.setattr("serialx.list_serial_ports", lambda: _TWO_PORTS)

    result = await _call(mode, "serial_open", {}, _answer("decline"))
    assert "cancelled" in _data(result)["error"]
    assert _sessions == {}


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_serial_open_without_elicitation_lists_ports(monkeypatch, mode):
    """Clients without elicitation should get the port list to relay."""
    monkeypatch.setattr("serialx.list_serial_ports", lambda: _TWO_PORTS)

    result = await _call(mode, "serial_open", {})
    assert _data(result)["available_ports"] == ["/dev/ttyA", "/dev/ttyB"]


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize(("callback", "expected"), [(_answer("accept", "9600 (80% readable)"), 9600), (None, 115200)])
async def test_serial_detect_baud_elicits_confirmation(monkeypatch, mode, callback, expected):
    """Should accept an elicited baud choice, or the top result without elicitation."""
    scans = []

    def fake_test(port, baud, probe):
        scans.append(baud)
        return {"baud_rate": baud, "readable_ratio": 0.9 if baud == 115200 else 0.8, "bytes_received": 4}

    monkeypatch.setattr("serial_mcp.server._COMMON_BAUD_RATES", [115200, 9600])
    monkeypatch.setattr("serial_mcp.server._test_baud_rate", fake_test)

    result = await _call(mode, "serial_detect_baud", {"port": "/dev/ttyTEST"}, callback)
    assert not result.is_error, result.content
    assert _data(result)["recommended"] == expected
    assert scans == [115200, 9600]  # the retry round reuses the first scan


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_tool_errors_reach_the_client(mode):
    """RuntimeError text should reach the model rather than a generic crash message."""
    result = await _call(mode, "serial_read", {})
    assert result.is_error
    assert "No sessions open" in result.content[0].text


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
