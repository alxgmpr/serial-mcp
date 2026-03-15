# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

serial-mcp is an MCP (Model Context Protocol) server that enables LLMs to communicate with serial devices (microcontrollers, routers, modems, embedded Linux). Python 3.10+, MIT licensed.

## Build & Run Commands

```bash
# Install (editable)
pip install -e .
# or
uv pip install -e .

# Run the MCP server
serial-mcp

# Install dependencies only
pip install -r requirements.txt
```

There are no tests, linting, or CI configured yet.

## Architecture

Two-file architecture in `serial_mcp/`:

- **server.py** — FastMCP server exposing ~25 tools and 3 prompts. Maintains a global `_sessions` dict keyed by port name. `_resolve_session()` auto-selects when only one session is open. Tools are grouped: port discovery, connection management, text read/write, binary/hex read/write, hardware signal control, and session utilities.

- **session.py** — `SerialSession` class managing individual serial connections. Runs a daemon background reader thread that stores data in a timestamped ring buffer (10MB default cap). Supports both destructive reads (`read_buffer`) and non-destructive historical reads (`read_since`). Thread safety via `threading.Lock` for history and `threading.Event` for data availability and shutdown signaling.

Entry point: `serial_mcp.server:main()` (registered as `serial-mcp` console script via pyproject.toml/Hatchling).

## Key Design Decisions

- **Timestamped ring buffer**: All received data is stored with timestamps, enabling `read_since()` for history replay without consuming the buffer. Automatic trimming adjusts the read cursor.
- **Pattern matching**: `command()` waits for a regex match OR 300ms of silence. `wait_for()` blocks until a pattern appears or timeout.
- **Hardware signals**: Full DTR/RTS control and CTS/DSR/RI/CD readback for reset sequences and bootloader entry.
- **Baud detection**: Tries 8 common rates, scores readability by printable ASCII ratio, optional `\r\n` probing.

## Dependencies

Only two: `mcp >= 1.0.0` and `pyserial >= 3.5`.
