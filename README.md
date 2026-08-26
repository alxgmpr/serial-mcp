# serial-mcp

MCP server that lets LLMs talk to serial devices: microcontrollers, routers, modems, embedded Linux, anything with a UART.

## Why this exists

LLMs are surprisingly good at interacting with hardware over serial, but without proper tooling they resort to hacking together Python scripts or asking you to copy-paste between terminals. This MCP server gives them a real serial interface instead.

What makes this different from the other serial MCP servers? I actually use this. Every tool exists because I hit a wall without it, not because it sounded good on a feature list. It handles things the others don't: XMODEM file transfers, hardware signal control for reset/bootloader sequences, baud rate detection, triggered responses for catching time-sensitive boot prompts, and a ring buffer that doesn't lose data between tool calls.

<img width="1456" height="1132" alt="image" src="https://github.com/user-attachments/assets/17e948ae-4888-4748-8694-77c1e257e329" />

## Install

### With `uv` (recommended)

Install globally so the `serial-mcp` command is available everywhere:

```sh
uv tool install serial-mcp
```

Or from a local clone:

```sh
uv tool install /path/to/serial-mcp
```

### With pip

```sh
pip install serial-mcp
```

### From source (editable)

```sh
git clone https://github.com/alxgmpr/serial-mcp.git
cd serial-mcp
uv pip install -e .
```

## Update

```sh
uv tool upgrade serial-mcp
```

Or with pip: `pip install --upgrade serial-mcp`

## Configure

### Claude Code

```sh
claude mcp add serial-mcp -- serial-mcp
```

That's it. Verify with `claude mcp list`.

If you installed from source instead of globally, use the full path:

```sh
claude mcp add serial-mcp -- python3 -m serial_mcp.server
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "serial": {
      "command": "serial-mcp"
    }
  }
}
```

### With `uvx` (no install)

```json
{
  "mcpServers": {
    "serial": {
      "command": "uvx",
      "args": ["serial-mcp"]
    }
  }
}
```

## Tools

Tools use the `serial_` prefix to avoid collisions, except for the discovery entry point `list_serial_ports`.

| Tool | What it does |
|---|---|
| `list_serial_ports` | List available ports with USB metadata (VID/PID, manufacturer) |
| `serial_detect_baud` | Try common baud rates and score readability to find the right one |
| `serial_force_release` | Kill the process holding a port (SIGTERM, then SIGKILL) so you can open it |
| `serial_open` | Open a connection (configurable baud, data bits, stop bits, parity, inactivity timeout) |
| `serial_close` | Close a connection and release the port |
| `serial_change_settings` | Change baud/parity/etc. on a live connection without closing |
| `serial_list_sessions` | List all open sessions |
| `serial_status` | Connection health, byte counts, uptime |
| `serial_command` | Send a string and wait for a response, with optional regex expect pattern |
| `serial_write` | Fire-and-forget text write |
| `serial_read` | Read buffered text data (advances the cursor) |
| `serial_read_since` | Read historical data since a timestamp (non-destructive, doesn't advance cursor) |
| `serial_wait_for` | Block until a regex pattern appears in incoming data |
| `serial_write_hex` | Write raw bytes as hex (`"AA 55 01 03"`) |
| `serial_read_hex` | Read buffered data as a hex string |
| `serial_set_signals` | Control DTR/RTS for reset sequences, bootloader entry, etc. |
| `serial_get_signals` | Read CTS, DSR, RI, CD signal state |
| `serial_send_break` | Send a serial break (used by U-Boot, Cisco ROMMON, etc.) |
| `serial_clear_history` | Flush the receive buffer |
| `serial_log_start` | Start capturing all received data to a file |
| `serial_log_stop` | Stop logging, return file path and stats |
| `serial_xmodem_send` | Send a file via XMODEM (checksum or CRC-16) |
| `serial_xmodem_receive` | Receive a file via XMODEM (checksum or CRC-16) |

`serial_wait_for` and `serial_command` both support triggered responses: you can set `respond` or `respond_hex` so the server automatically transmits a reply the instant a pattern matches. This is useful for catching time-sensitive prompts like U-Boot's "Hit any key to stop autoboot" where the MCP round-trip would be too slow.

The reader thread pauses during XMODEM transfers so the protocol has exclusive port access.

`serial_open`, `serial_status`, and `serial_list_sessions` return explicit lifecycle metadata for every open session:

| Field | Meaning |
|---|---|
| `cleanup_required` | `true` while the session owns the serial port |
| `cleanup_tool` | Tool to call when finished (`serial_close`) |
| `inactivity_timeout` | Configured inactivity period in seconds |
| `last_activity_at` | Latest session activity as a Unix timestamp |
| `auto_close_at` | Current inactivity deadline as a Unix timestamp |
| `auto_close_in` | Approximate seconds remaining until that deadline |

The deadline moves forward whenever data is read, received, or written. It is the exact point at which the session exceeds its inactivity allowance; the background reaper closes an expired session on its next check, currently within 30 seconds.

## Prompts

Four prompts guide common workflows:

| Prompt | Description |
|---|---|
| `scan_devices` | Walk through identifying all connected serial devices |
| `detect_baud_rate` | Run baud detection on a port and interpret the results |
| `interactive_shell` | Open a connection and probe for the device's shell prompt |
| `safe_session` | Open/use/close lifecycle with mandatory port release reminder |

## Quick example

```
1. list_serial_ports()                        → find /dev/ttyUSB0
2. serial_open(port="/dev/ttyUSB0")           → connect at 115200 8N1
3. serial_command(data="", expect="[$#]")     → get the shell prompt
4. serial_command(data="uname -a", expect="\\$")
5. serial_close()                              → release the port
```

For unknown devices, run `serial_detect_baud()` before opening the port. Binary protocols use the hex read/write tools, while `serial_wait_for(..., respond=" ")` can catch time-sensitive boot prompts.

## How it works

Each `serial_open()` creates a `SerialSession` with a background thread that reads from the port into a timestamped ring buffer (10MB default cap). Data is captured continuously, even between tool calls, so nothing gets lost. `serial_read_since()` can replay history without advancing the read cursor, and `serial_command()`/`serial_wait_for()` scan the buffer for regex matches as data arrives.

Sessions auto-close after a configurable inactivity timeout (default 15 minutes). Lifecycle metadata gives the model the latest activity time and current auto-close deadline rather than only a relative countdown. A background reaper checks every 30 seconds and closes stale sessions. When the AI next tries to use a closed session, it gets a clear error explaining what happened. All tools are async, with blocking serial I/O wrapped in `asyncio.to_thread()`.

Serial output from text tools is normalized (`\r\n` → `\n`, trailing whitespace stripped). Binary/hex tools return raw data.

When a port is held by another process, `serial_open` identifies the blocker via `lsof` and returns the PID and command name so the AI can offer to force-release it.

## Testing

No hardware required. Tests use a `MockSerial` fixture:

```sh
uv pip install -e ".[dev]"
pytest -v
```

Smoke-test the live server with the MCP Inspector:

```sh
DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector -- python3 -m serial_mcp.server
```

## Requirements

- Python >= 3.10
- [pyserial](https://pyserial.readthedocs.io/) >= 3.5
- [mcp](https://github.com/modelcontextprotocol/python-sdk) >= 1.28, < 2
- [pydantic](https://docs.pydantic.dev/) >= 2, < 3

## License

MIT
