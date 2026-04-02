# serial-mcp Improvements Design Spec

**Date:** 2026-04-02
**Version:** 0.3.0 -> 0.4.0

## Overview

Four workstreams to improve serial-mcp, executed in order:

1. CI/Linting (foundation)
2. New tools: XMODEM file transfer + log-to-file capture
3. Elicitation: port picker + baud rate confirmation
4. MCPB packaging for distribution

---

## 1. CI/Linting

### Tools

- **Ruff** — linting + formatting (replaces flake8/black/isort)
- **pytest** + **pytest-asyncio** — test framework
- **GitHub Actions** — CI on push + PR to main

### Configuration

`pyproject.toml` additions:

- `[tool.ruff]`: target Python 3.10, line-length 120, standard rule set
- `[tool.pytest.ini_options]`: testpaths = `["tests"]`
- `[project.optional-dependencies]` dev group: `ruff`, `pytest`, `pytest-asyncio`

### CI Workflow

`.github/workflows/ci.yml`:

- Trigger: push + PR to `main`
- Matrix: Python 3.10, 3.11, 3.12, 3.13
- Steps: checkout, setup-python, install deps + dev deps, `ruff check`, `ruff format --check`, `pytest`

### Test Infrastructure

`tests/` directory with:

- `conftest.py` — mock fixtures for `serial.Serial` and `serial.tools.list_ports` (no real hardware in CI)
- Unit tests for `SerialSession` (buffer management, read cursor, pattern matching, history trimming)
- Unit tests for server tools (session resolution, argument validation, error paths)

---

## 2. New Tools — XMODEM File Transfer

### Tools

- **`serial_xmodem_send(file_path, session_id=None, mode="xmodem")`**
  - Sends a file over XMODEM protocol
  - Modes: `"xmodem"` (128-byte blocks, checksum), `"xmodem-crc"` (128-byte blocks, CRC-16)
  - Returns: `bytes_sent`, `block_count`, `duration`
  - MCP annotations: `destructiveHint: true`, `readOnlyHint: false`

- **`serial_xmodem_receive(file_path, timeout=60.0, session_id=None, mode="xmodem")`**
  - Receives a file over XMODEM protocol
  - Same mode options as send
  - Returns: `bytes_received`, `block_count`, `duration`
  - MCP annotations: `destructiveHint: true`, `readOnlyHint: false`

### Implementation

New file: `serial_mcp/xmodem.py` (~200 lines)

- Pure-Python XMODEM implementation — no new dependency
- Takes read/write callables (abstracted from serial port), making it testable without hardware
- Protocol constants: SOH (0x01), EOT (0x04), ACK (0x06), NAK (0x15), CAN (0x18)
- Block format: SOH + block_num + ~block_num + 128 bytes data + checksum/CRC
- CRC-16 implementation included (XMODEM polynomial 0x1021)

### Session Integration

- During transfer, the session's background reader thread is **paused** (XMODEM needs exclusive serial port access)
- `SerialSession` gets `pause_reader()` / `resume_reader()` methods using the existing `_stop_event` mechanism
- Reader thread resumes after transfer completes (success or failure)

### Why No External Dependency

- The `xmodem` PyPI package is unmaintained
- Protocol is simple enough for ~200 lines
- Keeps dependency count at 2 (mcp + pyserial)
- Full control over session reader thread integration

---

## 3. New Tools — Log to File

### Tools

- **`serial_log_start(file_path, session_id=None, append=False)`**
  - Starts logging all received data to a file
  - One active log per session (error if already logging)
  - Returns: confirmation with `file_path`
  - MCP annotations: `destructiveHint: true`, `readOnlyHint: false`

- **`serial_log_stop(session_id=None)`**
  - Stops active logging, closes file handle
  - Returns: `bytes_logged`, `duration`, `file_path`
  - MCP annotations: `destructiveHint: false`, `readOnlyHint: false`

### Implementation

Changes to `serial_mcp/session.py`:

- New instance attributes: `_log_file` (optional file handle), `_log_start_time`, `_log_bytes`
- The existing `_reader_loop` writes each received chunk to the log file when logging is active (single-threaded, no extra lock needed)
- `start_logging()` / `stop_logging()` synchronize via the existing `_lock`
- Log format: `[2026-04-02T14:30:05.123] <data>\n` — plain text, one line per chunk, UTF-8 decoded with replacement characters for non-printable bytes
- `close()` and atexit cleanup stop active logging gracefully
- `start_logging(file_path, append)` and `stop_logging()` methods on `SerialSession`

---

## 4. Elicitation — Port Picker & Baud Confirmation

### Modified Tools

**`serial_open(port=None, ...)`** — port becomes `Optional[str]`:

- If `port` is None and elicitation is supported: list available ports, elicit user to pick one from an enum of port paths
- If `port` is None and elicitation not supported: return port list as text, ask LLM to relay the choice
- If `port` is provided: current behavior unchanged

**`serial_detect_baud(port, probe=True)`** — adds elicitation after detection:

- After scoring baud rates, if elicitation is supported: present top 3 candidates as enum (e.g., `"115200 (98% readable)"`)
- If elicitation not supported: return results as-is (current behavior, no change)

### Implementation

- Both tools receive `ctx: Context` parameter from FastMCP
- Capability check via `try/except CapabilityNotSupported` (canonical FastMCP pattern)
- Port picker: `await ctx.elicit("Select port", response_type=["COM3", "/dev/ttyUSB0", ...])`
- Baud confirmation: `await ctx.elicit("Confirm baud rate", response_type=["115200 (98%)", "9600 (42%)", ...])`
- Graceful degradation — tools work identically without elicitation support

### No New Tools

This modifies existing tool signatures only. No new tool names added.

---

## 5. MCPB Packaging

### Bundle Structure

```
serial-mcp.mcpb
├── manifest.json
├── server/
│   ├── run.py              # Entry script, prepends vendor/ to sys.path
│   ├── serial_mcp/         # Copied package source
│   └── vendor/             # pip install -t vendor (mcp, pyserial, transitive deps)
└── icon.png
```

### Manifest

```json
{
  "$schema": "https://raw.githubusercontent.com/anthropics/mcpb/main/schemas/mcpb-manifest-v0.4.schema.json",
  "manifest_version": "0.4",
  "name": "serial-mcp",
  "version": "0.4.0",
  "description": "Communicate with serial devices — microcontrollers, routers, modems, embedded Linux.",
  "author": { "name": "Alex Gompper" },
  "server": {
    "type": "python",
    "entry_point": "server/run.py",
    "mcp_config": {
      "command": "python3",
      "args": ["${__dirname}/server/run.py"]
    }
  },
  "compatibility": {
    "platforms": ["darwin", "win32", "linux"],
    "runtimes": { "python": ">=3.10" }
  },
  "homepage": "https://github.com/alxgmpr/serial-mcp",
  "repository": "https://github.com/alxgmpr/serial-mcp",
  "license": "MIT",
  "keywords": ["serial", "uart", "embedded", "iot", "pyserial", "xmodem"]
}
```

No `user_config` — serial ports are discovered at runtime.

### Build Script

`scripts/build-mcpb.sh`:

1. Clean `build/mcpb/`
2. Copy `serial_mcp/` into `build/mcpb/server/`
3. Write `run.py` entry script
4. `pip install -t build/mcpb/server/vendor -r requirements.txt`
5. Copy `manifest.json` and icon
6. `npx @anthropic-ai/mcpb pack`

### Platform Notes

- pyserial is pure Python — cross-platform vendoring works without native extension issues
- Python 3.10+ required on user's machine (MCPB `type: "python"` does not bundle an interpreter)
- Binary build (PyInstaller/Nuitka) deferred — adds significant complexity for marginal gain given target audience likely has Python

---

## File Changes Summary

### New Files

| File | Purpose |
|---|---|
| `serial_mcp/xmodem.py` | Pure-Python XMODEM send/receive |
| `tests/conftest.py` | Mock serial port fixtures |
| `tests/test_session.py` | SerialSession unit tests |
| `tests/test_server.py` | Server tool unit tests |
| `tests/test_xmodem.py` | XMODEM protocol unit tests |
| `.github/workflows/ci.yml` | CI workflow |
| `scripts/build-mcpb.sh` | MCPB build script |
| `manifest.json` | MCPB manifest (project root) |

### Modified Files

| File | Changes |
|---|---|
| `pyproject.toml` | Ruff config, pytest config, dev dependencies, version bump |
| `serial_mcp/server.py` | New tools (xmodem_send, xmodem_receive, log_start, log_stop), elicitation in serial_open and serial_detect_baud |
| `serial_mcp/session.py` | Logging support (_log_file, start/stop_logging), reader pause/resume for XMODEM |
| `requirements.txt` | No changes (no new runtime dependencies) |

### Dependency Changes

- **Runtime:** None (stays at `mcp` + `pyserial`)
- **Dev:** `ruff`, `pytest`, `pytest-asyncio` added as optional dev dependencies
