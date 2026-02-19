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

### With `pip`

```sh
pip install .
```

### With `uv` (recommended)

```sh
uv pip install .
```

### From requirements

```sh
pip install -r requirements.txt
```

## Configure

Add to your MCP client config. The exact format depends on the client:

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

### Claude Code (`.claude/settings.json`)

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

### Port discovery

| Tool | Description |
|---|---|
| `list_serial_ports` | List all available serial ports with USB metadata (VID/PID, manufacturer) |
| `detect_baud` | Auto-detect baud rate by trying common rates and scoring ASCII readability |

### Connection management

| Tool | Description |
|---|---|
| `open` | Open a serial connection (baud, data bits, stop bits, parity, timeout) |
| `close` | Close a connection |
| `change_settings` | Change baud/parity/etc. on a live connection without closing it |
| `list_sessions` | List all open sessions |
| `status` | Detailed connection health, byte counts, uptime |

### Read / write (text)

| Tool | Description |
|---|---|
| `command` | Send a string, wait for response. Supports `expect` regex for prompt detection |
| `write` | Fire-and-forget text write |
| `read` | Read buffered data (advances cursor) |
| `read_since` | Read historical data since a timestamp (non-destructive) |
| `wait_for` | Block until a regex pattern appears in incoming data |

### Read / write (binary)

| Tool | Description |
|---|---|
| `write_hex` | Write raw bytes as hex (`"AA 55 01 03"`) |
| `read_hex` | Read buffered data as hex string |

### Hardware signals

| Tool | Description |
|---|---|
| `set_signals` | Control DTR/RTS (reset micros, enter bootloader, etc.) |
| `get_signals` | Read DTR, RTS, CTS, DSR, RI, CD |
| `send_break` | Send a serial break (interrupt U-Boot, Cisco ROMMON, etc.) |

### Session utilities

| Tool | Description |
|---|---|
| `clear_history` | Flush the receive buffer and free memory |

## Usage examples

### Interactive shell on a Linux device

```
1. list_serial_ports()           → find /dev/ttyUSB0
2. open(port="/dev/ttyUSB0")     → connect at 115200 8N1
3. command(data="", expect="[$#]")  → get the shell prompt
4. command(data="uname -a", expect="\\$")
```

### Arduino / microcontroller

```
1. list_serial_ports()           → find /dev/ttyACM0
2. open(port="/dev/ttyACM0", baud_rate=9600)
3. command(data="STATUS", timeout=2)
4. set_signals(dtr=False)        → reset the board
5. set_signals(dtr=True)
6. wait_for(pattern="Ready", timeout=5)
```

### Unknown baud rate

```
1. detect_baud(port="/dev/ttyUSB0")  → recommends 9600
2. open(port="/dev/ttyUSB0", baud_rate=9600)
```

### Binary protocol (Modbus, etc.)

```
1. open(port="/dev/ttyUSB0", baud_rate=9600)
2. write_hex(hex_string="01 03 00 00 00 0A C5 CD")
3. read_hex(timeout=2)
```

### ESP32 bootloader entry

```
1. open(port="/dev/ttyUSB0", baud_rate=115200)
2. set_signals(dtr=False, rts=True)
3. set_signals(dtr=True, rts=False)
4. set_signals(dtr=False)
5. wait_for(pattern="waiting for download", timeout=3)
```

## How it works

Each `open()` call creates a `SerialSession` with a background thread that continuously reads from the port into a timestamped ring buffer (default 10MB cap). This means:

- **No data loss** — bytes are captured even between tool calls
- **Non-destructive reads** — `read_since()` can replay history without advancing the cursor
- **Pattern matching** — `command()` and `wait_for()` scan the buffer for regex matches in real-time
- **Multiple sessions** — each port gets its own thread and buffer

## Requirements

- Python >= 3.10
- [pyserial](https://pyserial.readthedocs.io/) >= 3.5
- [mcp](https://github.com/modelcontextprotocol/python-sdk) >= 1.0.0

## License

MIT
