import argparse
import asyncio
import atexit
import logging
import os
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Literal

import serial
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import Settings
from pydantic import create_model
from serial.tools import list_ports

from serial_mcp.session import SerialSession

_DEFAULT_INACTIVITY_TIMEOUT = 900  # 15 minutes
_DEFAULT_MAX_OUTPUT_BYTES = 16_384
_REAPER_INTERVAL = 30
_CORE_TOOLS = {
    "list_serial_ports",
    "serial_open",
    "serial_close",
    "serial_execute",
    "serial_command",
    "serial_detect_baud",
    "serial_status",
}

_sessions: dict[str, SerialSession] = {}
_session_timeouts: dict[str, float] = {}
_auto_closed_sessions: dict[str, str] = {}

logger = logging.getLogger("serial_mcp")

# MCP 1.29 leaves Settings.lifespan as an unresolved forward reference.
# Rebuilding is safe to remove once modelcontextprotocol/python-sdk#3294 ships.
Settings.model_rebuild()


def _cleanup_sessions():
    """Close all open sessions on server shutdown."""
    for session in list(_sessions.values()):
        session.close()
    _sessions.clear()
    _session_timeouts.clear()


atexit.register(_cleanup_sessions)


async def _session_reaper():
    """Background task that closes sessions after inactivity."""
    while True:
        await asyncio.sleep(_REAPER_INTERVAL)
        stale_ports = []
        for port, session in list(_sessions.items()):
            timeout = _session_timeouts.get(port, _DEFAULT_INACTIVITY_TIMEOUT)
            if session.inactivity_seconds > timeout:
                stale_ports.append(port)

        for port in stale_ports:
            session = _sessions.pop(port, None)
            timeout = _session_timeouts.pop(port, _DEFAULT_INACTIVITY_TIMEOUT)
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
                msg = f"Session on {port} was automatically closed after {int(timeout // 60)} minutes of inactivity."
                _auto_closed_sessions[port] = msg
                logger.info(msg)


@asynccontextmanager
async def _lifespan(app):
    reaper_task = asyncio.create_task(_session_reaper())
    try:
        yield {}
    finally:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass


mcp = FastMCP(
    "serial_mcp",
    instructions=(
        "Serial ports are exclusive resources. After calling serial_open(), call serial_close() before ending the "
        "task, switching to unrelated work, or abandoning the operation. Do not report completion until "
        "serial_close succeeds. If closing fails, report the session_id and auto-close deadline. Sessions may remain "
        "open across related tool calls and automatically close after their configured inactivity timeout. "
        "serial_execute() opens and closes its own session and requires no separate serial_close() call."
    ),
    lifespan=_lifespan,
)
mcp._mcp_server.version = version("pyserial-mcp")


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_respond(respond: str | None, respond_hex: str | None, encoding: str) -> bytes | None:
    """Resolve respond/respond_hex parameters into bytes for on_match_send."""
    respond = respond or None
    respond_hex = respond_hex or None
    if respond is not None and respond_hex is not None:
        raise ValueError("Cannot set both respond and respond_hex. Use one or the other.")
    if respond is not None:
        return respond.encode(encoding)
    if respond_hex is not None:
        try:
            return bytes.fromhex(respond_hex.replace(" ", ""))
        except ValueError as e:
            raise ValueError(f"Invalid respond_hex: {e}. Expected format: '7F' or 'AA 55 01'") from e
    return None


def _normalize_output(data: str) -> str:
    """Normalize serial output for AI consumption."""
    data = data.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in data.split("\n")]
    return "\n".join(lines).rstrip()


def _validate_output_limit(max_output_bytes: int) -> None:
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be at least 1")


def _limit_text_result(result: dict, max_output_bytes: int, encoding: str) -> dict:
    """Limit textual result data to its newest bytes and add truncation metadata."""
    _validate_output_limit(max_output_bytes)
    data = result.get("data", "")
    encoded = data.encode(encoding, errors="replace")
    truncated = len(encoded) > max_output_bytes
    if truncated:
        encoded = encoded[-max_output_bytes:]
        data = encoded.decode(encoding, errors="ignore")
        result["data"] = data

    returned_bytes = len(encoded) if truncated else result.get("byte_count", len(encoded))
    result["truncated"] = truncated
    result["returned_bytes"] = returned_bytes
    result["omitted_bytes"] = max(0, result.get("byte_count", returned_bytes) - returned_bytes)

    matched = result.get("matched")
    if matched is not None:
        matched_bytes = matched.encode(encoding, errors="replace")
        if len(matched_bytes) > max_output_bytes:
            result["matched"] = matched_bytes[-max_output_bytes:].decode(encoding, errors="ignore")
            result["matched_truncated"] = True
    return result


def _limit_hex_result(result: dict, max_output_bytes: int) -> dict:
    """Limit a hex result to its newest raw bytes and add truncation metadata."""
    _validate_output_limit(max_output_bytes)
    raw = bytes.fromhex(result.get("hex", ""))
    truncated = len(raw) > max_output_bytes
    returned = raw[-max_output_bytes:] if truncated else raw
    result["hex"] = returned.hex(" ")
    result["truncated"] = truncated
    result["returned_bytes"] = len(returned)
    result["omitted_bytes"] = max(0, result.get("byte_count", len(raw)) - len(returned))
    return result


def _session_lifecycle(session: SerialSession, inactivity_timeout: float) -> dict:
    """Return cleanup requirements and the session's exact inactivity deadline."""
    last_activity_at = session.last_activity
    auto_close_at = last_activity_at + inactivity_timeout
    return {
        "cleanup_required": True,
        "cleanup_tool": "serial_close",
        "inactivity_timeout": inactivity_timeout,
        "last_activity_at": int(last_activity_at),
        "auto_close_at": int(auto_close_at),
        "auto_close_in": max(0, round(auto_close_at - time.time(), 1)),
    }


async def _elicit_choice(ctx: Context, message: str, choices: list[str]) -> str | None:
    """Ask the client to select one of a bounded set of string values."""
    selection_type = Literal.__getitem__(tuple(choices))
    choice_model = create_model("Choice", selection=(selection_type, ...))
    result = await ctx.elicit(message, schema=choice_model)
    if result.action != "accept" or result.data is None:
        return None
    return result.data.selection


def _get_process_holding_port(port: str) -> dict | None:
    """Find the process holding a serial port using lsof."""
    try:
        result = subprocess.run(
            ["lsof", "-t", port],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        pid = int(result.stdout.strip().split()[0])
        ps_result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid=,user=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if ps_result.returncode == 0 and ps_result.stdout.strip():
            parts = ps_result.stdout.strip().split(None, 2)
            return {
                "pid": int(parts[0]),
                "user": parts[1] if len(parts) > 1 else "unknown",
                "command": parts[2] if len(parts) > 2 else "unknown",
            }
        return {"pid": pid, "user": "unknown", "command": "unknown"}
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def _resolve_session(session_id: str | None = None) -> SerialSession:
    """Resolve a session by ID, or auto-select when only one is open."""
    if session_id is not None and session_id in _auto_closed_sessions:
        msg = _auto_closed_sessions.pop(session_id)
        raise RuntimeError(msg + " Reopen with serial_open() if needed.")

    if session_id is None and _auto_closed_sessions:
        msgs = list(_auto_closed_sessions.values())
        _auto_closed_sessions.clear()
        raise RuntimeError(" ".join(msgs) + " Reopen with serial_open() if needed.")

    if session_id is not None:
        if session_id not in _sessions:
            available = list(_sessions.keys()) or "none"
            raise RuntimeError(
                f"No session open on '{session_id}'. "
                f"Open sessions: {available}. "
                f"Use list_serial_ports() to discover available ports, "
                f"then serial_open() to connect."
            )
        return _sessions[session_id]

    if len(_sessions) == 0:
        raise RuntimeError(
            "No sessions open. Use list_serial_ports() to discover available ports, then serial_open() to connect."
        )
    if len(_sessions) == 1:
        return next(iter(_sessions.values()))
    raise RuntimeError(f"Multiple sessions open ({list(_sessions.keys())}). Specify session_id to select one.")


# ── Port discovery ───────────────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def list_serial_ports() -> list[dict]:
    """List all available serial ports on the system.

    Returns device path, description, hardware ID, and USB metadata
    (vendor/product IDs, manufacturer, serial number) when available.
    Use this to discover which TTL adapters or serial devices are connected.
    """
    results = []
    for p in list_ports.comports():
        info: dict = {
            "device": p.device,
            "description": p.description,
            "hwid": p.hwid,
        }
        # USB metadata — only present for USB-serial adapters
        if p.vid is not None:
            info["usb"] = {
                "vid": f"0x{p.vid:04X}",
                "pid": f"0x{p.pid:04X}" if p.pid is not None else None,
                "manufacturer": p.manufacturer,
                "product": p.product,
                "serial_number": p.serial_number,
                "location": p.location,
            }
        results.append(info)
    return results


# ── Port control ────────────────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_force_release(port: str) -> dict:
    """Kill the process holding a serial port so it can be opened.

    Uses lsof to find the process holding the port, then sends SIGTERM
    (escalating to SIGKILL if needed). This is a destructive operation —
    it will terminate the process holding the port.

    Args:
        port: Serial port device path (e.g. /dev/ttyUSB0, /dev/cu.usbserial-1420)
    """
    proc = await asyncio.to_thread(_get_process_holding_port, port)
    if proc is None:
        return {
            "port": port,
            "released": False,
            "message": f"No process found holding {port}. The port may be free.",
        }

    pid = proc["pid"]
    command = proc["command"]

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {
            "port": port,
            "released": True,
            "pid": pid,
            "command": command,
            "message": f"Process {pid} ({command}) already exited.",
        }
    except PermissionError:
        return {
            "port": port,
            "released": False,
            "pid": pid,
            "command": command,
            "message": f"Permission denied killing PID {pid} ({command}). May need sudo.",
        }

    await asyncio.sleep(1.0)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {
            "port": port,
            "released": True,
            "pid": pid,
            "command": command,
            "signal": "SIGTERM",
            "message": f"Killed PID {pid} ({command}) with SIGTERM. Port {port} should now be free.",
        }

    # Still alive — escalate
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

    await asyncio.sleep(0.5)

    try:
        os.kill(pid, 0)
        still_alive = True
    except ProcessLookupError:
        still_alive = False

    if still_alive:
        return {
            "port": port,
            "released": False,
            "pid": pid,
            "command": command,
            "message": f"Failed to kill PID {pid} ({command}). Process may require sudo to terminate.",
        }

    return {
        "port": port,
        "released": True,
        "pid": pid,
        "command": command,
        "signal": "SIGKILL",
        "message": f"Killed PID {pid} ({command}) with SIGKILL. Port {port} should now be free.",
    }


# ── Connection management ────────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_open(
    ctx: Context,
    port: str | None = None,
    baud_rate: int = 115200,
    data_bits: Literal[5, 6, 7, 8] = 8,
    stop_bits: float = 1,
    parity: Literal["none", "even", "odd", "mark", "space"] = "none",
    timeout: float = 1.0,
    inactivity_timeout: float = 900,
) -> dict:
    """Open a serial connection to the specified port.

    If port is omitted, automatically discovers available ports. When only one
    port is found it is used directly; when multiple are found, elicitation is
    used to let the user pick one.

    IMPORTANT: Always call serial_close() when you are finished with the port.
    Leaving a port open prevents other processes from accessing the device.
    The session will be automatically closed after inactivity_timeout seconds
    of no activity. The result includes cleanup_required, cleanup_tool, and the
    current projected auto-close deadline as a Unix timestamp.

    Common configurations:
    - Most devices: 115200 baud, 8N1 (the defaults)
    - Older equipment: 9600 baud, 8N1
    - Use serial_detect_baud() first if unsure of the baud rate.

    Args:
        port: Serial port device path (e.g. /dev/ttyUSB0, COM3). Optional — omit to auto-discover.
        baud_rate: Baud rate for the connection
        data_bits: Number of data bits (5, 6, 7, or 8)
        stop_bits: Number of stop bits (1, 1.5, or 2)
        parity: Parity checking ("none", "even", "odd", "mark", "space")
        timeout: Read timeout in seconds
        inactivity_timeout: Seconds of inactivity before the session is auto-closed (default 900 = 15 min)
    """
    # If no port specified, try to elicit a choice
    if port is None:
        ports = [p.device for p in list_ports.comports()]
        if not ports:
            raise RuntimeError("No serial ports found. Check that a device is connected.")
        if len(ports) == 1:
            port = ports[0]
        else:
            try:
                port = await _elicit_choice(
                    ctx,
                    "Multiple serial ports found. Select one:",
                    ports,
                )
                if port is None:
                    return {"error": "Port selection cancelled. Call serial_open(port=...) with a specific port."}
            except Exception:
                # Elicitation not supported — return port list for the LLM to relay
                return {
                    "available_ports": ports,
                    "message": "Multiple ports found. Please call serial_open() again with one of the listed ports.",
                }

    if port in _sessions:
        raise RuntimeError(
            f"A session is already open on {port}. Close it first with serial_close(), "
            f"or use serial_change_settings() to modify the connection."
        )

    if stop_bits not in (1, 1.5, 2):
        raise ValueError(f"Invalid stop_bits: {stop_bits}. Must be 1, 1.5, or 2.")

    try:
        session = await asyncio.to_thread(
            SerialSession,
            port=port,
            baud_rate=baud_rate,
            data_bits=data_bits,
            stop_bits=stop_bits,
            parity=parity,
            timeout=timeout,
        )
    except (serial.SerialException, PermissionError, OSError) as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in ("busy", "permission", "access", "locked", "in use")):
            proc = _get_process_holding_port(port)
            if proc:
                raise RuntimeError(
                    f"Port {port} is in use by PID {proc['pid']} ({proc['command']}). "
                    f'Close that process or use serial_force_release(port="{port}") to kill it.'
                ) from e
            raise RuntimeError(
                f"Port {port} is in use by another process. "
                f"Close any other serial terminals (minicom, screen, picocom, cu, PuTTY, etc.) "
                f'or use serial_force_release(port="{port}") to kill the holder.'
            ) from e
        raise RuntimeError(
            f"Could not open port {port}: {e}. Check that the device is connected and the port path is correct."
        ) from e

    _sessions[port] = session
    _session_timeouts[port] = inactivity_timeout

    return {
        "session_id": port,
        "message": f"Connected to {port} at {baud_rate} baud ({data_bits}{parity[0].upper()}{stop_bits})",
        "connected_at": int(session.connected_at),
        **_session_lifecycle(session, inactivity_timeout),
    }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_close(session_id: str | None = None) -> str:
    """Close a serial connection and release the port.

    Always call this when you are done interacting with a device. Leaving a port
    open blocks other tools and processes from accessing the device.

    Args:
        session_id: Port name of the session to close. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    port = session.port
    await asyncio.to_thread(session.close)
    del _sessions[port]
    _session_timeouts.pop(port, None)
    return f"Closed connection to {port}."


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def serial_change_settings(
    session_id: str | None = None,
    baud_rate: int | None = None,
    data_bits: Literal[5, 6, 7, 8] | None = None,
    stop_bits: float | None = None,
    parity: Literal["none", "even", "odd", "mark", "space"] | None = None,
) -> dict:
    """Change serial port settings on an open connection without closing it.

    Useful when a device changes baud rate mid-session (e.g. bootloader
    hands off to OS at a different speed) or during manual baud detection.

    Args:
        session_id: Port name of the session. Optional if only one session is open.
        baud_rate: New baud rate (e.g. 9600, 115200). None to keep current.
        data_bits: New data bits (5, 6, 7, or 8). None to keep current.
        stop_bits: New stop bits (1, 1.5, or 2). None to keep current.
        parity: New parity ("none", "even", "odd", "mark", "space"). None to keep current.
    """
    session = _resolve_session(session_id)

    kwargs = {}
    if baud_rate is not None:
        kwargs["baud_rate"] = baud_rate
    if data_bits is not None:
        kwargs["data_bits"] = data_bits
    if stop_bits is not None:
        if stop_bits not in (1, 1.5, 2):
            raise ValueError(f"Invalid stop_bits: {stop_bits}. Must be 1, 1.5, or 2.")
        kwargs["stop_bits"] = stop_bits
    if parity is not None:
        kwargs["parity"] = parity

    if not kwargs:
        raise ValueError("No settings provided. Specify at least one of: baud_rate, data_bits, stop_bits, parity.")

    session.change_settings(**kwargs)

    return {
        "session_id": session.port,
        "baud_rate": session.baud_rate,
        "data_bits": session.data_bits,
        "stop_bits": session.stop_bits,
        "parity": session.parity,
    }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_execute(
    ctx: Context,
    data: str,
    port: str | None = None,
    baud_rate: int = 115200,
    expect: str | None = None,
    timeout: float = 5.0,
    encoding: str = "utf-8",
    append_newline: bool = True,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Open 8N1, run one text command, and always close.

    Replaces open/command/close for a single command. Use an explicit session
    for multi-step, binary, non-8N1, or triggered-response work.

    Args:
        data: Text to send
        port: Port path; omit to choose automatically
        baud_rate: Connection speed
        expect: Response regex
        timeout: Response deadline in seconds
        encoding: Text encoding
        append_newline: Whether to append CRLF
    """
    opened = await serial_open(ctx, port=port, baud_rate=baud_rate)
    session_id = opened.get("session_id")
    if session_id is None:
        return opened

    try:
        result = await serial_command(
            data=data,
            expect=expect,
            timeout=timeout,
            session_id=session_id,
            encoding=encoding,
            append_newline=append_newline,
            max_output_bytes=max_output_bytes,
        )
    finally:
        await serial_close(session_id)

    result.pop("session_id", None)
    result["port"] = session_id
    result["closed"] = True
    return result


# ── Command / expect ─────────────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_command(
    data: str,
    expect: str | None = None,
    timeout: float = 5.0,
    session_id: str | None = None,
    encoding: str = "utf-8",
    append_newline: bool = True,
    respond: str | None = None,
    respond_hex: str | None = None,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Send a command and wait for the response. This is the primary tool for
    interacting with serial devices — it combines write + read into a single
    atomic operation.

    If `expect` is provided, waits until that regex pattern appears in the
    response. Without `expect`, waits for the device to stop sending (300ms
    of silence after last received byte).

    If `respond` or `respond_hex` is provided along with `expect`, the response
    is sent immediately when the pattern matches — before this tool returns.
    This enables sub-millisecond triggered responses for time-sensitive sequences.
    The respond string is sent as-is (no newline appended).

    Examples:
        - Linux shell: serial_command(data="ls -la", expect="\\\\$")
        - AT modem:    serial_command(data="AT", expect="OK|ERROR")
        - Router CLI:  serial_command(data="show version", expect="#")
        - Simple ping: serial_command(data="hello", timeout=2)
        - Reboot + catch bootloader: serial_command(data="reboot", expect="Hit any key", respond=" ")

    Args:
        data: Text to send to the device
        expect: Regex pattern to wait for in the response (e.g. "\\\\$", "OK", ">")
        timeout: Max seconds to wait for response (default 5)
        session_id: Port name of the session. Optional if only one session is open.
        encoding: Character encoding (default utf-8)
        append_newline: Whether to append \\r\\n to the data (default True)
        respond: Text to send immediately when expect pattern matches (sent as-is, no newline)
        respond_hex: Hex bytes to send when expect pattern matches (e.g. "7F", "AA 55")
    """
    session = _resolve_session(session_id)
    on_match_send = _resolve_respond(respond, respond_hex, encoding)

    if on_match_send is not None and not expect:
        raise ValueError(
            "respond/respond_hex requires expect to be set. "
            "Use serial_wait_for for pattern-triggered responses without sending a command first."
        )

    if append_newline:
        data += "\r\n"

    raw = data.encode(encoding)
    result = await asyncio.to_thread(
        session.command, raw, expect=expect, timeout=timeout, encoding=encoding, on_match_send=on_match_send
    )
    if "data" in result:
        result["data"] = _normalize_output(result["data"])
    if "matched" in result and result["matched"] is not None:
        result["matched"] = _normalize_output(result["matched"])
    _limit_text_result(result, max_output_bytes, encoding)
    result["session_id"] = session.port
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_wait_for(
    pattern: str,
    timeout: float = 10.0,
    session_id: str | None = None,
    encoding: str = "utf-8",
    respond: str | None = None,
    respond_hex: str | None = None,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Wait for a specific pattern to appear in the serial output. Blocks until
    the regex pattern matches in incoming data, or until timeout.

    If `respond` or `respond_hex` is provided, that data is sent immediately
    when the pattern matches — before this tool returns. This enables
    sub-millisecond triggered responses for time-sensitive sequences like
    interrupting a bootloader autoboot. The respond string is sent as-is
    (no newline appended).

    Useful for waiting for boot messages, login prompts, or specific device
    states before interacting.

    Examples:
        - Wait for login:      serial_wait_for(pattern="login:")
        - Wait for U-Boot:     serial_wait_for(pattern="U-Boot", timeout=30)
        - Wait for prompt:     serial_wait_for(pattern="[$#>]\\\\s*$")
        - Wait for ready:      serial_wait_for(pattern="System ready", timeout=60)
        - Interrupt autoboot:  serial_wait_for(pattern="Hit any key to stop autoboot", respond=" ", timeout=60)
        - Bootloader handshake: serial_wait_for(pattern="Bootloader v", respond_hex="7F")

    Args:
        pattern: Regex pattern to wait for
        timeout: Max seconds to wait (default 10)
        session_id: Port name of the session. Optional if only one session is open.
        encoding: Character encoding (default utf-8)
        respond: Text to send immediately when pattern matches (sent as-is, no newline)
        respond_hex: Hex bytes to send when pattern matches (e.g. "7F", "AA 55")
    """
    session = _resolve_session(session_id)
    on_match_send = _resolve_respond(respond, respond_hex, encoding)
    result = await asyncio.to_thread(
        session.wait_for, pattern=pattern, timeout=timeout, encoding=encoding, on_match_send=on_match_send
    )
    if "data" in result:
        result["data"] = _normalize_output(result["data"])
    if "matched" in result and result["matched"] is not None:
        result["matched"] = _normalize_output(result["matched"])
    _limit_text_result(result, max_output_bytes, encoding)
    result["session_id"] = session.port
    return result


# ── Text read/write ──────────────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_write(
    data: str,
    session_id: str | None = None,
    encoding: str = "utf-8",
    append_newline: bool = True,
) -> dict:
    """Write data to the open serial port.

    For most interactions, prefer serial_command() which writes and waits for the
    response in one step. Use serial_write() for fire-and-forget or when you need
    manual timing control.

    Args:
        data: Text to send over serial
        session_id: Port name of the session to write to. Optional if only one session is open.
        encoding: Character encoding to use
        append_newline: Whether to append \\r\\n to the data
    """
    session = _resolve_session(session_id)

    if append_newline:
        data += "\r\n"

    raw = data.encode(encoding)
    count = await asyncio.to_thread(session.write, raw)
    return {"bytes_written": count, "session_id": session.port}


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def serial_read(
    session_id: str | None = None,
    timeout: float = 1.0,
    encoding: str = "utf-8",
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Read all buffered data from the serial port.

    Returns everything received since the last read, then advances the cursor.
    If no new data is available, waits up to timeout seconds for data to arrive.

    For most interactions, prefer serial_command() which writes and reads in one step.
    Use serial_read() when passively monitoring or after a manual serial_write().

    Args:
        session_id: Port name of the session to read from. Optional if only one session is open.
        timeout: Seconds to wait for data if buffer is empty
        encoding: Character encoding for decoding the data
    """
    session = _resolve_session(session_id)
    result = await asyncio.to_thread(session.read_buffer, timeout=timeout, encoding=encoding)
    if "data" in result:
        result["data"] = _normalize_output(result["data"])
    _limit_text_result(result, max_output_bytes, encoding)
    result["session_id"] = session.port
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def serial_read_since(
    session_id: str | None = None,
    since: float | None = None,
    encoding: str = "utf-8",
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Read historical data received since a given timestamp (non-destructive).

    Unlike serial_read(), this does NOT advance the read cursor — calling serial_read_since
    will not affect what serial_read() returns next. If since is omitted, returns all
    data received since the session was opened.

    Args:
        session_id: Port name of the session. Optional if only one session is open.
        since: Unix timestamp. If omitted, returns all data since session start.
        encoding: Character encoding for decoding the data
    """
    session = _resolve_session(session_id)
    result = session.read_since(since=since, encoding=encoding)
    if "data" in result:
        result["data"] = _normalize_output(result["data"])
    _limit_text_result(result, max_output_bytes, encoding)
    result["session_id"] = session.port
    result["connected_at"] = int(session.connected_at)
    if result["time_range"] is not None:
        result["time_range"] = {name: int(value) for name, value in result["time_range"].items()}
    return result


# ── Binary / hex read/write ──────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_write_hex(
    hex_string: str,
    session_id: str | None = None,
) -> dict:
    """Write raw bytes (specified as hex) to the serial port.

    Use this for binary protocols (Modbus, bootloader commands, firmware
    upload, raw UART framing) where you need exact byte-level control.
    No newline is appended.

    Examples:
        - Send Modbus query: serial_write_hex(hex_string="01 03 00 00 00 0A C5 CD")
        - Send break byte:   serial_write_hex(hex_string="FF")
        - STM32 bootloader:  serial_write_hex(hex_string="7F")

    Args:
        hex_string: Hex-encoded bytes separated by spaces (e.g. "AA 55 01 03 FF")
        session_id: Port name of the session. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    try:
        raw = bytes.fromhex(hex_string.replace(" ", ""))
    except ValueError as e:
        raise ValueError(f"Invalid hex string: {e}. Expected format: 'AA 55 01 03' or 'AA550103'") from e
    count = await asyncio.to_thread(session.write, raw)
    return {
        "bytes_written": count,
        "hex_sent": raw.hex(" "),
        "session_id": session.port,
    }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def serial_read_hex(
    session_id: str | None = None,
    timeout: float = 1.0,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Read buffered data as hex-encoded bytes (for binary protocols).

    Like serial_read() but returns data as a hex string instead of decoded text.
    Advances the read cursor.

    Args:
        session_id: Port name of the session to read from. Optional if only one session is open.
        timeout: Seconds to wait for data if buffer is empty
    """
    session = _resolve_session(session_id)
    result = await asyncio.to_thread(session.read_buffer_hex, timeout=timeout)
    _limit_hex_result(result, max_output_bytes)
    result["session_id"] = session.port
    return result


# ── Hardware signals ─────────────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def serial_set_signals(
    dtr: bool | None = None,
    rts: bool | None = None,
    session_id: str | None = None,
) -> dict:
    """Control DTR and RTS hardware signals on the serial port.

    These pins are commonly used to:
    - Reset microcontrollers (DTR on Arduino, DTR+RTS on ESP32)
    - Enter bootloader/programming mode
    - Control power to peripherals via transistor switches
    - Implement hardware flow control

    Examples:
        - Reset Arduino:      serial_set_signals(dtr=False); serial_set_signals(dtr=True)
        - ESP32 bootloader:   serial_set_signals(dtr=False, rts=True) then
                              serial_set_signals(dtr=True, rts=False)

    Args:
        dtr: Set DTR signal high (True) or low (False). None leaves it unchanged.
        rts: Set RTS signal high (True) or low (False). None leaves it unchanged.
        session_id: Port name of the session. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    return session.set_signals(dtr=dtr, rts=rts)


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def serial_get_signals(session_id: str | None = None) -> dict:
    """Read the current state of all serial control signals.

    Returns: DTR, RTS (output signals you control) and CTS, DSR, RI, CD
    (input signals from the remote device). Useful for checking hardware
    flow control state or verifying device presence.

    Args:
        session_id: Port name of the session. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    result = session.get_signals()
    result["session_id"] = session.port
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_send_break(
    duration: float = 0.25,
    session_id: str | None = None,
) -> dict:
    """Send a serial break signal.

    A break signal holds the TX line low for longer than a character frame,
    which many devices interpret as a special command:
    - U-Boot: interrupt autoboot to get a shell
    - Cisco IOS: break into ROMMON
    - Sun/Oracle ILOM: enter diagnostics
    - Linux SysRq: trigger magic SysRq if configured

    Args:
        duration: Break duration in seconds (default 0.25, most devices need 0.1-0.5)
        session_id: Port name of the session. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    await asyncio.to_thread(session.send_break, duration)
    return {
        "break_sent": True,
        "duration": duration,
        "session_id": session.port,
    }


# ── Baud rate detection ──────────────────────────────────────────────

_COMMON_BAUD_RATES = [115200, 9600, 57600, 38400, 19200, 4800, 2400, 1200]


def _test_baud_rate(port: str, baud: int, probe: bool) -> dict | None:
    """Test a single baud rate. Returns result dict or None on failure."""
    try:
        s = serial.Serial(port, baud, timeout=0.5)
        time.sleep(0.1)

        # Drain any stale data
        if s.in_waiting:
            s.read(s.in_waiting)

        if probe:
            s.write(b"\r\n")
            time.sleep(0.5)
        else:
            time.sleep(1.0)

        data = b""
        if s.in_waiting:
            data = s.read(s.in_waiting)

        s.close()

        if data:
            printable = sum(1 for b in data if 32 <= b <= 126 or b in (10, 13, 9))
            ratio = round(printable / len(data), 2)
            return {
                "baud_rate": baud,
                "readable_ratio": ratio,
                "bytes_received": len(data),
                "sample": data.decode("ascii", errors="replace")[:200],
            }
    except serial.SerialException:
        pass
    return None


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def serial_detect_baud(
    ctx: Context,
    port: str,
    probe: bool = True,
) -> dict:
    """Auto-detect the baud rate on a serial port by trying common rates and
    checking which one produces readable ASCII output.

    Opens and closes the port internally — the port must NOT have an active
    session. After detection, use serial_open() with the recommended baud rate.

    If `probe` is True (default), sends \\r\\n at each baud rate to elicit a
    response. Set to False for passive listening (e.g. if the device sends
    data continuously).

    Args:
        port: Serial port device path (e.g. /dev/ttyUSB0, COM3)
        probe: Whether to send \\r\\n to prompt a response (default True)
    """
    if port in _sessions:
        raise RuntimeError(f"Port {port} has an active session. Close it first with serial_close().")

    results = []
    for i, baud in enumerate(_COMMON_BAUD_RATES):
        await ctx.report_progress(i, len(_COMMON_BAUD_RATES))
        result = await asyncio.to_thread(_test_baud_rate, port, baud, probe)
        if result:
            results.append(result)

    results.sort(key=lambda x: x["readable_ratio"], reverse=True)

    if not results:
        return {
            "port": port,
            "results": results,
            "recommended": None,
            "message": ("No data received at any baud rate. Check wiring and that the device is powered on."),
        }

    # Try to elicit baud rate confirmation
    top_results = results[:3]
    choices = [f"{r['baud_rate']} ({int(r['readable_ratio'] * 100)}% readable)" for r in top_results]

    selected_baud = results[0]["baud_rate"]
    try:
        selected_choice = await _elicit_choice(
            ctx,
            f"Baud rate detection complete for {port}. Confirm rate:",
            choices,
        )
        if selected_choice is not None:
            # Parse baud rate from the selection string (e.g., "115200 (98% readable)")
            selected_baud = int(selected_choice.split(" ")[0])
    except Exception:
        # Elicitation not supported — use top result
        pass

    return {
        "port": port,
        "results": results,
        "recommended": selected_baud,
        "message": (
            f"Best match: {selected_baud} baud "
            f"({int(next(r['readable_ratio'] for r in results if r['baud_rate'] == selected_baud) * 100)}% readable)"
        ),
    }


# ── Session management ───────────────────────────────────────────────


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def serial_clear_history(session_id: str | None = None) -> dict:
    """Clear the receive history buffer for a session.

    Resets the read cursor and frees memory. Useful for long-running sessions
    on chatty devices, or to get a clean slate before a new interaction.

    Args:
        session_id: Port name of the session. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    session.clear_history()
    return {"cleared": True, "session_id": session.port}


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def serial_log_start(
    file_path: str,
    session_id: str | None = None,
    append: bool = False,
) -> dict:
    """Start logging all received serial data to a file.

    Creates a timestamped log file capturing everything the device sends.
    Similar to minicom's capture feature. Only one log file per session.

    Args:
        file_path: Path to the log file to create/write
        session_id: Port name of the session. Optional if only one session is open.
        append: If True, append to existing file instead of overwriting
    """
    session = _resolve_session(session_id)
    session.start_logging(file_path, append=append)
    return {
        "logging": True,
        "file_path": file_path,
        "append": append,
        "session_id": session.port,
    }


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def serial_log_stop(session_id: str | None = None) -> dict:
    """Stop logging serial data and close the log file.

    Returns the log file path, total bytes logged, and duration.

    Args:
        session_id: Port name of the session. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    result = session.stop_logging()
    result["session_id"] = session.port
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_xmodem_send(
    file_path: str,
    session_id: str | None = None,
    mode: Literal["xmodem", "xmodem-crc"] = "xmodem",
) -> dict:
    """Send a file to the device using XMODEM protocol.

    The device must already be waiting to receive (e.g. after a "rx" command
    or entering a bootloader's receive mode). Supports standard XMODEM
    (checksum) and XMODEM-CRC (CRC-16) modes.

    Args:
        file_path: Path to the file to send
        session_id: Port name of the session. Optional if only one session is open.
        mode: "xmodem" for checksum mode, "xmodem-crc" for CRC-16 mode
    """
    import os

    from serial_mcp.xmodem import xmodem_send

    session = _resolve_session(session_id)

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Pause reader thread — XMODEM needs exclusive port access
    session.pause_reader()
    try:
        with open(file_path, "rb") as f:
            result = await asyncio.to_thread(
                xmodem_send,
                f,
                session.raw_read,
                session.raw_write,
                mode=mode,
            )
    finally:
        session.resume_reader()

    result["session_id"] = session.port
    result["file_path"] = file_path
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def serial_xmodem_receive(
    file_path: str,
    timeout: float = 60.0,
    session_id: str | None = None,
    mode: Literal["xmodem", "xmodem-crc"] = "xmodem",
) -> dict:
    """Receive a file from the device using XMODEM protocol.

    The device must already be sending (e.g. after a "sx filename" command).
    The received file is written to file_path.

    Args:
        file_path: Path where the received file will be saved
        timeout: Max seconds to wait for transfer to complete
        session_id: Port name of the session. Optional if only one session is open.
        mode: "xmodem" for checksum mode, "xmodem-crc" for CRC-16 mode
    """
    from serial_mcp.xmodem import xmodem_receive

    session = _resolve_session(session_id)

    # Pause reader thread — XMODEM needs exclusive port access
    session.pause_reader()
    try:
        with open(file_path, "wb") as f:
            result = await asyncio.to_thread(
                xmodem_receive,
                f,
                session.raw_read,
                session.raw_write,
                mode=mode,
                timeout=timeout,
            )
    finally:
        session.resume_reader()

    result["session_id"] = session.port
    result["file_path"] = file_path
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def serial_list_sessions() -> dict:
    """List all open serial sessions with connection and cleanup details.

    Each session includes its latest activity timestamp and projected auto-close
    deadline as Unix timestamps. The deadline moves whenever the session has
    new read/write activity.
    """
    sessions_list = []
    for s in _sessions.values():
        timeout = _session_timeouts.get(s.port, _DEFAULT_INACTIVITY_TIMEOUT)
        sessions_list.append(
            {
                "session_id": s.port,
                "baud_rate": s.baud_rate,
                "healthy": s.is_healthy,
                "uptime_seconds": round(s.uptime, 1),
                "connected_at": int(s.connected_at),
                "idle_seconds": round(s.inactivity_seconds, 1),
                **_session_lifecycle(s, timeout),
            }
        )
    return {
        "session_count": len(_sessions),
        "sessions": sessions_list,
    }


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def serial_status(session_id: str | None = None) -> dict:
    """Get the current serial session status including connection health.

    Reports whether the device is still connected, bytes buffered, total
    bytes received, connection parameters, and health status. If the USB
    adapter has been physically disconnected, the health field will indicate
    the problem. Also reports the latest activity timestamp and projected
    auto-close deadline as Unix timestamps.

    Args:
        session_id: Port name of the session. Optional if only one session is open.
                    If omitted with multiple sessions open, returns a summary of all.
    """
    if not _sessions:
        return {
            "connected": False,
            "message": "No sessions open. Use list_serial_ports() to find devices.",
        }

    # If multiple sessions and no session_id, return summary of all
    if session_id is None and len(_sessions) > 1:
        summary = []
        for s in _sessions.values():
            t = _session_timeouts.get(s.port, _DEFAULT_INACTIVITY_TIMEOUT)
            summary.append(
                {
                    "session_id": s.port,
                    "baud_rate": s.baud_rate,
                    "healthy": s.is_healthy,
                    "bytes_in_buffer": s.bytes_in_buffer,
                    "total_bytes_received": s.total_bytes_received,
                    "uptime_seconds": round(s.uptime, 1),
                    "connected_at": int(s.connected_at),
                    "idle_seconds": round(s.inactivity_seconds, 1),
                    **_session_lifecycle(s, t),
                }
            )
        return {
            "connected": True,
            "session_count": len(_sessions),
            "sessions": summary,
        }

    session = _resolve_session(session_id)
    health = session.health_status
    t = _session_timeouts.get(session.port, _DEFAULT_INACTIVITY_TIMEOUT)
    return {
        "connected": True,
        "session_id": session.port,
        "baud_rate": session.baud_rate,
        "data_bits": session.data_bits,
        "stop_bits": session.stop_bits,
        "parity": session.parity,
        "healthy": health["healthy"],
        "health_reason": health.get("reason"),
        "bytes_in_buffer": session.bytes_in_buffer,
        "total_bytes_received": session.total_bytes_received,
        "uptime_seconds": round(session.uptime, 1),
        "connected_at": int(session.connected_at),
        "idle_seconds": round(session.inactivity_seconds, 1),
        **_session_lifecycle(session, t),
    }


_TOOL_DESCRIPTIONS = {
    "list_serial_ports": "List available serial ports and USB metadata.",
    "serial_force_release": "Terminate the process holding a serial port. Destructive.",
    "serial_open": "Open a serial session. Close it with serial_close when finished.",
    "serial_close": "Close a serial session and release its port.",
    "serial_change_settings": "Change settings on an open serial session.",
    "serial_execute": "Open 8N1, run one text command, and always close.",
    "serial_command": "Send text and collect output until a regex matches, timeout, or silence.",
    "serial_wait_for": "Wait for a regex in incoming output, optionally responding on match.",
    "serial_write": "Write text without waiting for a response.",
    "serial_read": "Consume buffered text, returning the newest bytes up to the output limit.",
    "serial_read_since": "Read history without consuming it, returning the newest bytes up to the limit.",
    "serial_write_hex": "Write raw bytes supplied as hexadecimal.",
    "serial_read_hex": "Consume buffered bytes and return limited hexadecimal output.",
    "serial_set_signals": "Set DTR or RTS on an open session.",
    "serial_get_signals": "Read modem-control signal states.",
    "serial_send_break": "Send a serial break signal.",
    "serial_detect_baud": "Try common baud rates and rank readable responses.",
    "serial_clear_history": "Clear a session's receive history.",
    "serial_log_start": "Start logging received data to a file.",
    "serial_log_stop": "Stop logging and return capture statistics.",
    "serial_xmodem_send": "Send a file with XMODEM checksum or CRC mode.",
    "serial_xmodem_receive": "Receive a file with XMODEM checksum or CRC mode.",
    "serial_list_sessions": "List open serial sessions and cleanup deadlines.",
    "serial_status": "Return connection health and session statistics.",
}

for _tool_name, _description in _TOOL_DESCRIPTIONS.items():
    mcp._tool_manager.get_tool(_tool_name).description = _description

_ALL_TOOL_REGISTRATIONS = dict(mcp._tool_manager._tools)


# ── MCP Prompts ──────────────────────────────────────────────────────


@mcp.prompt()
def scan_devices() -> str:
    """Scan and identify all connected serial devices."""
    return (
        "Scan for connected serial devices and report what you find:\n"
        "1. Call list_serial_ports() to discover all available ports\n"
        "2. For each port, note the USB VID/PID to identify the adapter type "
        "(FTDI, CP2102, CH340, etc.)\n"
        "3. Report your findings: device path, adapter type, and any other "
        "identifying information\n"
        "4. Suggest likely baud rates based on the device type"
    )


@mcp.prompt()
def detect_baud_rate(port: str) -> str:
    """Detect the correct baud rate for a serial device."""
    return (
        f"Detect the correct baud rate on port {port}:\n"
        f'1. Call serial_detect_baud(port="{port}") to try common baud rates\n'
        "2. Review the readable_ratio for each result — higher means more "
        "likely correct\n"
        "3. Report the recommended baud rate and confidence level\n"
        "4. If confident, offer to open a connection at the detected rate"
    )


@mcp.prompt()
def interactive_shell(port: str, baud_rate: int = 115200) -> str:
    """Open an interactive serial shell session."""
    return (
        f"Start an interactive session on {port} at {baud_rate} baud:\n"
        f'1. Call serial_open(port="{port}", baud_rate={baud_rate})\n'
        "2. Send a few carriage returns to wake the device: "
        'serial_command(data="", timeout=2)\n'
        "3. Examine the response to identify the device and its prompt\n"
        "4. You are now ready to send commands. Use serial_command() with the "
        "expect parameter set to the device's prompt pattern for reliable "
        "interaction.\n"
        "5. When you are finished, ALWAYS call serial_close() to release the port."
    )


@mcp.prompt()
def safe_session(port: str, baud_rate: int = 115200) -> str:
    """Open a serial session with a reminder to close it when done."""
    return (
        f"Open a serial session on {port} at {baud_rate} baud and interact with the device.\n"
        f'1. Call serial_open(port="{port}", baud_rate={baud_rate})\n'
        "2. Perform your task using serial_command() or other serial tools\n"
        "3. When COMPLETELY DONE — even if an error occurred — call serial_close() to release the port\n"
        "\n"
        "CRITICAL: You MUST call serial_close() before finishing. Failure to close the port "
        "will prevent other processes from accessing the device."
    )


# ── Entrypoint ───────────────────────────────────────────────────────


def _apply_tool_profile(profile: str) -> None:
    """Expose either the full tool set or the common-workflow subset."""
    if profile != "core":
        if profile != "full":
            raise ValueError(f"Unknown tool profile: {profile}")
        mcp._tool_manager._tools.clear()
        mcp._tool_manager._tools.update(_ALL_TOOL_REGISTRATIONS)
        return

    mcp._tool_manager._tools.clear()
    mcp._tool_manager._tools.update(_ALL_TOOL_REGISTRATIONS)
    for tool in mcp._tool_manager.list_tools():
        if tool.name not in _CORE_TOOLS:
            mcp.remove_tool(tool.name)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="MCP server for serial devices")
    parser.add_argument(
        "--profile",
        choices=("full", "core"),
        default=os.environ.get("SERIAL_MCP_TOOL_PROFILE", "full"),
        help="Tool set to expose (default: full; env: SERIAL_MCP_TOOL_PROFILE)",
    )
    args = parser.parse_args(argv)
    _apply_tool_profile(args.profile)
    mcp.run()


if __name__ == "__main__":
    main()
