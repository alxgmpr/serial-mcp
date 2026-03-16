# serial-mcp

MCP server for serial port communication. Lets LLMs talk to hardware — microcontrollers, routers, modems, embedded Linux, anything with a UART.

## What it does

Exposes serial ports as MCP tools so an AI assistant can:

- **Discover** connected USB-serial adapters and identify them by VID/PID
- **Connect** to devices with configurable baud rate, data bits, stop bits, parity
- **Send commands** and wait for responses (with regex-based expect patterns)
- **Read/write raw hex** for binary protocols (Modbus, bootloader commands, etc.)
- **Control hardware signals** (DTR/RTS) — reset Arduinos, enter ESP32 bootloader mode
- **Auto-detect baud rate** by trying common rates and scoring readability
- **Manage multiple sessions** simultaneously across different ports

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

All tools are prefixed with `serial_` to avoid name collisions with other MCP servers. Each tool includes MCP annotations (`readOnlyHint`, `destructiveHint`, etc.).

### Port discovery

| Tool | Description |
|---|---|
| `list_serial_ports` | List available serial ports with USB metadata (VID/PID, manufacturer) |
| `serial_detect_baud` | Auto-detect baud rate by trying common rates and scoring ASCII readability |

### Connection management

| Tool | Description |
|---|---|
| `serial_open` | Open a serial connection (baud, data bits, stop bits, parity, timeout) |
| `serial_close` | Close a connection |
| `serial_change_settings` | Change baud/parity/etc. on a live connection without closing it |
| `serial_list_sessions` | List all open sessions |
| `serial_status` | Detailed connection health, byte counts, uptime |

### Read / write (text)

| Tool | Description |
|---|---|
| `serial_command` | Send a string, wait for response. Supports `expect` regex for prompt detection |
| `serial_write` | Fire-and-forget text write |
| `serial_read` | Read buffered data (advances cursor) |
| `serial_read_since` | Read historical data since a timestamp (non-destructive) |
| `serial_wait_for` | Block until a regex pattern appears in incoming data |

### Read / write (binary)

| Tool | Description |
|---|---|
| `serial_write_hex` | Write raw bytes as hex (`"AA 55 01 03"`) |
| `serial_read_hex` | Read buffered data as hex string |

### Hardware signals

| Tool | Description |
|---|---|
| `serial_set_signals` | Control DTR/RTS (reset micros, enter bootloader, etc.) |
| `serial_get_signals` | Read DTR, RTS, CTS, DSR, RI, CD |
| `serial_send_break` | Send a serial break (interrupt U-Boot, Cisco ROMMON, etc.) |

### Session utilities

| Tool | Description |
|---|---|
| `serial_clear_history` | Flush the receive buffer and free memory |

## Usage examples

### Interactive shell on a Linux device

```
1. list_serial_ports()                        → find /dev/ttyUSB0
2. serial_open(port="/dev/ttyUSB0")           → connect at 115200 8N1
3. serial_command(data="", expect="[$#]")     → get the shell prompt
4. serial_command(data="uname -a", expect="\\$")
```

### Arduino / microcontroller

```
1. list_serial_ports()                        → find /dev/ttyACM0
2. serial_open(port="/dev/ttyACM0", baud_rate=9600)
3. serial_command(data="STATUS", timeout=2)
4. serial_set_signals(dtr=False)              → reset the board
5. serial_set_signals(dtr=True)
6. serial_wait_for(pattern="Ready", timeout=5)
```

### Unknown baud rate

```
1. serial_detect_baud(port="/dev/ttyUSB0")    → recommends 9600
2. serial_open(port="/dev/ttyUSB0", baud_rate=9600)
```

### Binary protocol (Modbus, etc.)

```
1. serial_open(port="/dev/ttyUSB0", baud_rate=9600)
2. serial_write_hex(hex_string="01 03 00 00 00 0A C5 CD")
3. serial_read_hex(timeout=2)
```

### ESP32 bootloader entry

```
1. serial_open(port="/dev/ttyUSB0", baud_rate=115200)
2. serial_set_signals(dtr=False, rts=True)
3. serial_set_signals(dtr=True, rts=False)
4. serial_set_signals(dtr=False)
5. serial_wait_for(pattern="waiting for download", timeout=3)
```

## How it works

Each `serial_open()` call creates a `SerialSession` with a background thread that continuously reads from the port into a timestamped ring buffer (default 10MB cap). This means:

- **No data loss** — bytes are captured even between tool calls
- **Non-destructive reads** — `serial_read_since()` can replay history without advancing the cursor
- **Pattern matching** — `serial_command()` and `serial_wait_for()` scan the buffer for regex matches in real-time
- **Multiple sessions** — each port gets its own thread and buffer

All tools are async. Blocking serial I/O runs in `asyncio.to_thread()` so the event loop stays free.

## Testing

```sh
DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector -- python3 -m serial_mcp.server
```

Set command to `python3` and args to `-m serial_mcp.server` in the inspector UI, then connect.

## Requirements

- Python >= 3.10
- [pyserial](https://pyserial.readthedocs.io/) >= 3.5
- [mcp](https://github.com/modelcontextprotocol/python-sdk) >= 1.0.0

## License

MIT
