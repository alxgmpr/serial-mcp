# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

serial-mcp is an MCP (Model Context Protocol) server that enables LLMs to communicate with serial devices (microcontrollers, routers, modems, embedded Linux). Python 3.10+, MIT licensed.

## Build & Run Commands

```bash
# Install (editable, with dev dependencies)
uv pip install -e ".[dev]"

# Run the MCP server
python3 -m serial_mcp.server

# Test with MCP Inspector
DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector -- python3 -m serial_mcp.server

# Install dependencies only
uv pip install -r requirements.txt

# Run tests
pytest -v

# Lint
ruff check serial_mcp/ tests/
ruff format --check serial_mcp/ tests/

# Build MCPB bundle
./scripts/build-mcpb.sh
```

## Architecture

Three-file architecture in `serial_mcp/`:

- **server.py** — FastMCP server exposing ~22 async tools (all prefixed `serial_*`) and 3 prompts. Maintains a global `_sessions` dict keyed by port name with atexit cleanup. `_resolve_session()` auto-selects when only one session is open. All tools include MCP annotations (readOnlyHint, destructiveHint, etc.). Blocking serial I/O is wrapped in `asyncio.to_thread()`. Tools are grouped: port discovery, connection management, text read/write, binary/hex read/write, hardware signal control, session utilities, XMODEM file transfer, and logging. Supports elicitation for interactive port and baud selection.

- **session.py** — `SerialSession` class managing individual serial connections. Runs a daemon background reader thread that stores data in a timestamped ring buffer (10MB default cap). Supports both destructive reads (`read_buffer`) and non-destructive historical reads (`read_since`). Thread safety via `threading.Lock` for history and `threading.Event` for data availability and shutdown signaling.

- **xmodem.py** — Pure-Python XMODEM send/receive. Takes read/write callables for testability. Supports checksum and CRC-16 modes.

Entry point: `serial_mcp.server:main()` (registered as `serial-mcp` console script via pyproject.toml/Hatchling).

## Key Design Decisions

- **Timestamped ring buffer**: All received data is stored with timestamps, enabling `read_since()` for history replay without consuming the buffer. Automatic trimming adjusts the read cursor.
- **Pattern matching**: `serial_command()` waits for a regex match OR 300ms of silence. `serial_wait_for()` blocks until a pattern appears or timeout.
- **Hardware signals**: Full DTR/RTS control and CTS/DSR/RI/CD readback for reset sequences and bootloader entry.
- **Baud detection**: Tries 8 common rates, scores readability by printable ASCII ratio, optional `\r\n` probing.
- **XMODEM file transfer**: Pure-Python implementation with reader thread pause/resume. Uses callable abstraction for testability.

## Testing

27 unit tests using pytest with a `MockSerial` fixture (no real hardware needed). Tests cover session buffer management, pattern matching, history trimming, logging, XMODEM protocol, and server tool resolution.

Run: `pytest -v` (requires dev dependencies: `uv pip install -e ".[dev]"`)

## Gotchas

- **XMODEM pauses the reader thread**: During `serial_xmodem_send`/`serial_xmodem_receive`, the background reader is stopped for exclusive port access. Logging and `read_buffer` won't capture data during transfers.
- **Elicitation fallback**: `serial_open` (portless) and `serial_detect_baud` use elicitation when supported. If the host doesn't support it, they return data for the LLM to relay — not an error.

## Dependencies

Only two runtime deps: `mcp >= 1.0.0` and `pyserial >= 3.5`. Dev: `ruff`, `pytest`, `pytest-asyncio`.

## Releasing

Releases are automated by `.github/workflows/release.yml` on tag push (`v*.*.*`). To cut a release: bump `version` in **both** `pyproject.toml` and `manifest.json`, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The workflow verifies the tag matches both files, runs lint + tests, builds sdist/wheel/`.mcpb`, publishes to PyPI via Trusted Publishing (gated by a manual approval), and creates a GitHub Release with the `.mcpb` attached. See [RELEASING.md](RELEASING.md) for full procedure and recovery steps.
