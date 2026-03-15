import asyncio
import atexit
import time
from typing import Literal

import serial
from mcp.server.fastmcp import FastMCP, Context
from serial.tools import list_ports

from serial_mcp.session import SerialSession

mcp = FastMCP("serial_mcp")

_sessions: dict[str, SerialSession] = {}


def _cleanup_sessions():
    """Close all open sessions on server shutdown."""
    for session in list(_sessions.values()):
        session.close()
    _sessions.clear()


atexit.register(_cleanup_sessions)


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_session(session_id: str | None = None) -> SerialSession:
    """Resolve a session by ID, or auto-select when only one is open."""
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
            "No sessions open. Use list_serial_ports() to discover "
            "available ports, then serial_open() to connect."
        )
    if len(_sessions) == 1:
        return next(iter(_sessions.values()))
    raise RuntimeError(
        f"Multiple sessions open ({list(_sessions.keys())}). "
        "Specify session_id to select one."
    )


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
    port: str,
    baud_rate: int = 115200,
    data_bits: Literal[5, 6, 7, 8] = 8,
    stop_bits: float = 1,
    parity: Literal["none", "even", "odd", "mark", "space"] = "none",
    timeout: float = 1.0,
) -> dict:
    """Open a serial connection to the specified port.

    Common configurations:
    - Most devices: 115200 baud, 8N1 (the defaults)
    - Older equipment: 9600 baud, 8N1
    - Use serial_detect_baud() first if unsure of the baud rate.

    Args:
        port: Serial port device path (e.g. /dev/ttyUSB0, COM3)
        baud_rate: Baud rate for the connection
        data_bits: Number of data bits (5, 6, 7, or 8)
        stop_bits: Number of stop bits (1, 1.5, or 2)
        parity: Parity checking ("none", "even", "odd", "mark", "space")
        timeout: Read timeout in seconds
    """
    if port in _sessions:
        raise RuntimeError(
            f"A session is already open on {port}. Close it first with serial_close(), "
            f"or use serial_change_settings() to modify the connection."
        )

    if stop_bits not in (1, 1.5, 2):
        raise ValueError(f"Invalid stop_bits: {stop_bits}. Must be 1, 1.5, or 2.")

    session = await asyncio.to_thread(
        SerialSession,
        port=port,
        baud_rate=baud_rate,
        data_bits=data_bits,
        stop_bits=stop_bits,
        parity=parity,
        timeout=timeout,
    )
    _sessions[port] = session

    return {
        "session_id": port,
        "message": (
            f"Connected to {port} at {baud_rate} baud "
            f"({data_bits}{parity[0].upper()}{stop_bits})"
        ),
        "connected_at": session.connected_at,
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
    """Close a serial connection.

    Args:
        session_id: Port name of the session to close. Optional if only one session is open.
    """
    session = _resolve_session(session_id)
    port = session.port
    await asyncio.to_thread(session.close)
    del _sessions[port]
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
) -> dict:
    """Send a command and wait for the response. This is the primary tool for
    interacting with serial devices — it combines write + read into a single
    atomic operation.

    If `expect` is provided, waits until that regex pattern appears in the
    response. Without `expect`, waits for the device to stop sending (300ms
    of silence after last received byte).

    Examples:
        - Linux shell: serial_command(data="ls -la", expect="\\\\$")
        - AT modem:    serial_command(data="AT", expect="OK|ERROR")
        - Router CLI:  serial_command(data="show version", expect="#")
        - Simple ping: serial_command(data="hello", timeout=2)

    Args:
        data: Text to send to the device
        expect: Regex pattern to wait for in the response (e.g. "\\\\$", "OK", ">")
        timeout: Max seconds to wait for response (default 5)
        session_id: Port name of the session. Optional if only one session is open.
        encoding: Character encoding (default utf-8)
        append_newline: Whether to append \\r\\n to the data (default True)
    """
    session = _resolve_session(session_id)

    if append_newline:
        data += "\r\n"

    raw = data.encode(encoding)
    result = await asyncio.to_thread(
        session.command, raw, expect=expect, timeout=timeout, encoding=encoding
    )
    result["session_id"] = session.port
    return result


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def serial_wait_for(
    pattern: str,
    timeout: float = 10.0,
    session_id: str | None = None,
    encoding: str = "utf-8",
) -> dict:
    """Wait for a specific pattern to appear in the serial output (without
    sending anything). Blocks until the regex pattern matches in incoming data,
    or until timeout.

    Useful for waiting for boot messages, login prompts, or specific device
    states before interacting.

    Examples:
        - Wait for login:  serial_wait_for(pattern="login:")
        - Wait for U-Boot: serial_wait_for(pattern="U-Boot", timeout=30)
        - Wait for prompt:  serial_wait_for(pattern="[$#>]\\\\s*$")
        - Wait for ready:   serial_wait_for(pattern="System ready", timeout=60)

    Args:
        pattern: Regex pattern to wait for
        timeout: Max seconds to wait (default 10)
        session_id: Port name of the session. Optional if only one session is open.
        encoding: Character encoding (default utf-8)
    """
    session = _resolve_session(session_id)
    result = await asyncio.to_thread(
        session.wait_for, pattern=pattern, timeout=timeout, encoding=encoding
    )
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
    result = await asyncio.to_thread(
        session.read_buffer, timeout=timeout, encoding=encoding
    )
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
    result["session_id"] = session.port
    result["connected_at"] = session.connected_at
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
        raise ValueError(
            f"Invalid hex string: {e}. "
            f"Expected format: 'AA 55 01 03' or 'AA550103'"
        ) from e
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
            printable = sum(
                1 for b in data
                if 32 <= b <= 126 or b in (10, 13, 9)
            )
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
        raise RuntimeError(
            f"Port {port} has an active session. Close it first with serial_close()."
        )

    results = []
    for i, baud in enumerate(_COMMON_BAUD_RATES):
        await ctx.report_progress(i, len(_COMMON_BAUD_RATES))
        result = await asyncio.to_thread(_test_baud_rate, port, baud, probe)
        if result:
            results.append(result)

    results.sort(key=lambda x: x["readable_ratio"], reverse=True)

    return {
        "port": port,
        "results": results,
        "recommended": results[0]["baud_rate"] if results else None,
        "message": (
            f"Best match: {results[0]['baud_rate']} baud "
            f"({int(results[0]['readable_ratio'] * 100)}% readable)"
            if results else
            "No data received at any baud rate. Check wiring and that "
            "the device is powered on."
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
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def serial_list_sessions() -> dict:
    """List all open serial sessions with connection details."""
    return {
        "session_count": len(_sessions),
        "sessions": [
            {
                "session_id": s.port,
                "baud_rate": s.baud_rate,
                "healthy": s.is_healthy,
                "uptime_seconds": round(s.uptime, 1),
                "connected_at": s.connected_at,
            }
            for s in _sessions.values()
        ],
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
    the problem.

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
        return {
            "connected": True,
            "session_count": len(_sessions),
            "sessions": [
                {
                    "session_id": s.port,
                    "baud_rate": s.baud_rate,
                    "healthy": s.is_healthy,
                    "bytes_in_buffer": s.bytes_in_buffer,
                    "total_bytes_received": s.total_bytes_received,
                    "uptime_seconds": round(s.uptime, 1),
                    "connected_at": s.connected_at,
                }
                for s in _sessions.values()
            ],
        }

    session = _resolve_session(session_id)
    health = session.health_status
    return {
        "connected": True,
        "session_id": session.port,
        "port": session.port,
        "baud_rate": session.baud_rate,
        "data_bits": session.data_bits,
        "stop_bits": session.stop_bits,
        "parity": session.parity,
        "healthy": health["healthy"],
        "health_reason": health.get("reason"),
        "bytes_in_buffer": session.bytes_in_buffer,
        "total_bytes_received": session.total_bytes_received,
        "uptime_seconds": round(session.uptime, 1),
        "connected_at": session.connected_at,
    }


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
        f"1. Call serial_detect_baud(port=\"{port}\") to try common baud rates\n"
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
        f"1. Call serial_open(port=\"{port}\", baud_rate={baud_rate})\n"
        "2. Send a few carriage returns to wake the device: "
        "serial_command(data=\"\", timeout=2)\n"
        "3. Examine the response to identify the device and its prompt\n"
        "4. You are now ready to send commands. Use serial_command() with the "
        "expect parameter set to the device's prompt pattern for reliable "
        "interaction."
    )


# ── Entrypoint ───────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
