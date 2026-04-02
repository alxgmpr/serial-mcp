# serial-mcp Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CI/linting, XMODEM file transfer, log-to-file capture, elicitation for port picker and baud confirmation, and MCPB packaging to serial-mcp.

**Architecture:** Four sequential workstreams building on each other. CI goes first so all subsequent code is tested and linted. New tools (XMODEM, logging) add a new module and extend session.py. Elicitation modifies two existing tools. MCPB packaging wraps everything for distribution.

**Tech Stack:** Python 3.10+, FastMCP, pyserial, ruff, pytest, pytest-asyncio, GitHub Actions, MCPB CLI.

**Spec:** `docs/superpowers/specs/2026-04-02-serial-mcp-improvements-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `tests/__init__.py` | Package marker |
| `tests/conftest.py` | Mock serial fixtures shared across all tests |
| `tests/test_session.py` | Unit tests for SerialSession |
| `tests/test_xmodem.py` | Unit tests for XMODEM protocol |
| `tests/test_server.py` | Unit tests for server tools |
| `serial_mcp/xmodem.py` | Pure-Python XMODEM send/receive implementation |
| `.github/workflows/ci.yml` | GitHub Actions CI workflow |
| `manifest.json` | MCPB manifest (project root) |
| `scripts/build-mcpb.sh` | MCPB build script |
| `scripts/run.py` | MCPB entry script (vendor path prepend) |

### Modified Files

| File | Changes |
|---|---|
| `pyproject.toml` | Ruff config, pytest config, dev deps, version bump, build excludes |
| `serial_mcp/session.py` | Logging (start/stop), reader pause/resume for XMODEM |
| `serial_mcp/server.py` | 4 new tools, elicitation in serial_open + serial_detect_baud |
| `.gitignore` | Add `build/`, `dist/`, `*.mcpb` |

---

## Task 1: CI/Linting — pyproject.toml configuration

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add ruff, pytest, and dev dependencies config to pyproject.toml**

Add the following sections to the end of `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "ruff>=0.4.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.ruff]
target-version = "py310"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create tests package**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 3: Install dev dependencies**

Run: `cd /Volumes/Secondary/serial-mcp && python3 -m venv .venv && source .venv/bin/activate && uv pip install -e ".[dev]"`

- [ ] **Step 4: Verify ruff runs clean on existing code**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/ruff check serial_mcp/`
Expected: Either clean, or fixable issues.

If there are issues, run: `.venv/bin/ruff check --fix serial_mcp/`
Then: `.venv/bin/ruff format serial_mcp/`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/__init__.py
git commit -m "chore: add ruff, pytest config and dev dependencies"
```

---

## Task 2: CI/Linting — GitHub Actions workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the CI workflow file**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff
      - run: ruff check serial_mcp/ tests/
      - run: ruff format --check serial_mcp/ tests/

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: pytest -v
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow for ruff and pytest"
```

---

## Task 3: CI/Linting — Test fixtures and first session tests

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Create mock serial fixtures in conftest.py**

Create `tests/conftest.py`:

```python
import threading
import time
from unittest.mock import MagicMock, PropertyMock

import pytest


class MockSerial:
    """A mock serial.Serial that simulates a serial port with injectable data."""

    def __init__(self, **kwargs):
        self.port = kwargs.get("port", "/dev/ttyTEST")
        self.baudrate = kwargs.get("baudrate", 115200)
        self.bytesize = kwargs.get("bytesize", 8)
        self.stopbits = kwargs.get("stopbits", 1)
        self.parity = kwargs.get("parity", "N")
        self.timeout = kwargs.get("timeout", 1.0)

        self._is_open = True
        self._input_buffer = bytearray()
        self._lock = threading.Lock()
        self._dtr = True
        self._rts = True

    @property
    def is_open(self):
        return self._is_open

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._input_buffer)

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        self._rts = value

    @property
    def cts(self):
        return False

    @property
    def dsr(self):
        return False

    @property
    def ri(self):
        return False

    @property
    def cd(self):
        return False

    def read(self, size=1):
        with self._lock:
            data = bytes(self._input_buffer[:size])
            del self._input_buffer[:size]
            return data

    def write(self, data):
        return len(data)

    def close(self):
        self._is_open = False

    def send_break(self, duration=0.25):
        pass

    def inject_data(self, data: bytes):
        """Test helper: simulate data arriving on the serial port."""
        with self._lock:
            self._input_buffer.extend(data)


@pytest.fixture
def mock_serial(monkeypatch):
    """Patches serial.Serial to return a MockSerial instance.

    Returns the MockSerial instance so tests can inject data.
    """
    instance = MockSerial()

    def fake_serial_init(**kwargs):
        instance.port = kwargs.get("port", instance.port)
        instance.baudrate = kwargs.get("baudrate", instance.baudrate)
        return instance

    monkeypatch.setattr("serial.Serial", lambda **kwargs: fake_serial_init(**kwargs))
    return instance
```

- [ ] **Step 2: Write first session test — buffer read**

Create `tests/test_session.py`:

```python
import time
import threading

from serial_mcp.session import SerialSession


def test_read_buffer_returns_injected_data(mock_serial):
    """Data injected into the mock port should be readable via read_buffer."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        # Give reader thread time to start
        time.sleep(0.05)

        mock_serial.inject_data(b"hello world")
        time.sleep(0.05)  # Let reader thread pick it up

        result = session.read_buffer(timeout=1.0)
        assert result["data"] == "hello world"
        assert result["byte_count"] == 11
    finally:
        session.close()


def test_read_buffer_empty_timeout(mock_serial):
    """read_buffer with no data should return empty after timeout."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        result = session.read_buffer(timeout=0.1)
        assert result["data"] == ""
        assert result["byte_count"] == 0
    finally:
        session.close()


def test_read_cursor_advances(mock_serial):
    """Second read_buffer call should only return new data."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        time.sleep(0.05)
        mock_serial.inject_data(b"first")
        time.sleep(0.05)

        result1 = session.read_buffer(timeout=1.0)
        assert result1["data"] == "first"

        mock_serial.inject_data(b"second")
        time.sleep(0.05)

        result2 = session.read_buffer(timeout=1.0)
        assert result2["data"] == "second"
    finally:
        session.close()


def test_read_since_non_destructive(mock_serial):
    """read_since should not advance the read cursor."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        time.sleep(0.05)
        mock_serial.inject_data(b"hello")
        time.sleep(0.05)

        result_since = session.read_since()
        assert result_since["data"] == "hello"

        # read_buffer should still return the same data (cursor not advanced)
        result_buf = session.read_buffer(timeout=1.0)
        assert result_buf["data"] == "hello"
    finally:
        session.close()


def test_history_trimming(mock_serial):
    """History should be trimmed when it exceeds max_history_bytes."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
        max_history_bytes=100,
    )
    try:
        time.sleep(0.05)
        # Inject more than 100 bytes in chunks
        for i in range(20):
            mock_serial.inject_data(b"x" * 10)
            time.sleep(0.02)

        time.sleep(0.1)

        # Buffer should be trimmed to ~100 bytes
        assert session._buffer_bytes <= 100
        # But total should reflect all received
        assert session._total_bytes_received == 200
    finally:
        session.close()


def test_pattern_matching(mock_serial):
    """wait_for should detect regex patterns in incoming data."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        time.sleep(0.05)

        # Inject data with a prompt pattern after a delay
        def delayed_inject():
            time.sleep(0.1)
            mock_serial.inject_data(b"Loading...\r\nroot@device:~# ")

        t = threading.Thread(target=delayed_inject)
        t.start()

        result = session.wait_for(r"#\s*$", timeout=2.0)
        assert result["timed_out"] is False
        assert "#" in result["matched"]
        t.join()
    finally:
        session.close()


def test_command_with_expect(mock_serial):
    """command() with expect should wait for the pattern."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        time.sleep(0.05)

        def delayed_response():
            time.sleep(0.1)
            mock_serial.inject_data(b"output line 1\r\nOK\r\n")

        t = threading.Thread(target=delayed_response)
        t.start()

        result = session.command(b"AT\r\n", expect="OK", timeout=2.0)
        assert result["timed_out"] is False
        assert "OK" in result["data"]
        t.join()
    finally:
        session.close()


def test_command_settle_mode(mock_serial):
    """command() without expect should wait for silence."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        time.sleep(0.05)

        def delayed_response():
            time.sleep(0.05)
            mock_serial.inject_data(b"response data")

        t = threading.Thread(target=delayed_response)
        t.start()

        result = session.command(b"test\r\n", timeout=2.0, settle_time=0.2)
        assert result["data"] == "response data"
        t.join()
    finally:
        session.close()


def test_health_status(mock_serial):
    """Healthy session should report healthy."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        assert session.is_healthy is True
        assert session.health_status == {"healthy": True}
    finally:
        session.close()


def test_close_clears_state(mock_serial):
    """close() should stop reader and clear history."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    time.sleep(0.05)
    mock_serial.inject_data(b"data")
    time.sleep(0.05)

    session.close()
    assert mock_serial.is_open is False
```

- [ ] **Step 3: Run the tests**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest tests/test_session.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_session.py
git commit -m "test: add session unit tests with mock serial fixtures"
```

---

## Task 4: Logging — session.py changes

**Files:**
- Modify: `serial_mcp/session.py`
- Create: `tests/test_logging.py`

- [ ] **Step 1: Write failing tests for logging**

Create `tests/test_logging.py`:

```python
import os
import time
import tempfile

from serial_mcp.session import SerialSession


def test_start_logging_creates_file(mock_serial):
    """start_logging should create the log file."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        session.start_logging(log_path)
        assert session.is_logging is True

        session.stop_logging()
        assert session.is_logging is False
        assert os.path.exists(log_path)
    finally:
        session.close()
        os.unlink(log_path)


def test_logging_captures_data(mock_serial):
    """Received data should appear in the log file."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        time.sleep(0.05)
        session.start_logging(log_path)

        mock_serial.inject_data(b"hello from device\r\n")
        time.sleep(0.1)  # Let reader thread pick up + write to log

        session.stop_logging()

        with open(log_path) as f:
            content = f.read()
        assert "hello from device" in content
        assert "[" in content  # Timestamp bracket
    finally:
        session.close()
        os.unlink(log_path)


def test_stop_logging_returns_stats(mock_serial):
    """stop_logging should return bytes_logged, duration, file_path."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        time.sleep(0.05)
        session.start_logging(log_path)

        mock_serial.inject_data(b"test data")
        time.sleep(0.1)

        result = session.stop_logging()
        assert result["file_path"] == log_path
        assert result["bytes_logged"] > 0
        assert result["duration"] >= 0
    finally:
        session.close()
        os.unlink(log_path)


def test_double_start_logging_raises(mock_serial):
    """Starting logging while already logging should raise."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        session.start_logging(log_path)
        try:
            session.start_logging(log_path)
            assert False, "Should have raised RuntimeError"
        except RuntimeError:
            pass

        session.stop_logging()
    finally:
        session.close()
        os.unlink(log_path)


def test_logging_append_mode(mock_serial):
    """append=True should add to existing file content."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False, mode="w") as f:
            f.write("existing content\n")
            log_path = f.name

        time.sleep(0.05)
        session.start_logging(log_path, append=True)

        mock_serial.inject_data(b"new data")
        time.sleep(0.1)

        session.stop_logging()

        with open(log_path) as f:
            content = f.read()
        assert "existing content" in content
        assert "new data" in content
    finally:
        session.close()
        os.unlink(log_path)


def test_close_stops_logging(mock_serial):
    """Closing the session should stop active logging."""
    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
    )
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        log_path = f.name

    try:
        session.start_logging(log_path)
        session.close()
        # Should not raise, logging should be stopped gracefully
        assert session.is_logging is False
    finally:
        os.unlink(log_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest tests/test_logging.py -v`
Expected: FAIL — `SerialSession` has no `start_logging`/`stop_logging`/`is_logging`.

- [ ] **Step 3: Implement logging in session.py**

Add these attributes to `SerialSession.__init__` (after line 55 in session.py, after `self._disconnect_reason`):

```python
        # Logging
        self._log_file = None
        self._log_start_time: float | None = None
        self._log_bytes = 0
```

Add log writing to `_reader_loop`, inside the `if data:` block (after `self._data_event.set()` on line 74), still inside the `try:` block:

```python
                    if data:
                        with self._lock:
                            self._history.append((time.time(), data))
                            self._buffer_bytes += len(data)
                            self._total_bytes_received += len(data)
                            self._trim_history()
                        self._data_event.set()
                        # Log to file if active
                        if self._log_file is not None:
                            try:
                                from datetime import datetime, timezone
                                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                                text = data.decode("utf-8", errors="replace")
                                self._log_file.write(f"[{ts}] {text}\n")
                                self._log_file.flush()
                                self._log_bytes += len(data)
                            except Exception:
                                pass  # Don't let logging errors kill the reader
```

Add the `start_logging`, `stop_logging`, and `is_logging` members (before the `# ── Properties` section, around line 346):

```python
    # ── Logging ─────────────────────────────────────────────────────

    @property
    def is_logging(self) -> bool:
        return self._log_file is not None

    def start_logging(self, file_path: str, append: bool = False) -> None:
        """Start logging received data to a file."""
        with self._lock:
            if self._log_file is not None:
                raise RuntimeError("Already logging. Call stop_logging() first.")
            mode = "a" if append else "w"
            self._log_file = open(file_path, mode, encoding="utf-8")
            self._log_start_time = time.time()
            self._log_bytes = 0
            self._log_path = file_path

    def stop_logging(self) -> dict:
        """Stop logging and close the log file. Returns stats."""
        with self._lock:
            if self._log_file is None:
                return {"file_path": None, "bytes_logged": 0, "duration": 0}
            self._log_file.close()
            result = {
                "file_path": self._log_path,
                "bytes_logged": self._log_bytes,
                "duration": round(time.time() - self._log_start_time, 1),
            }
            self._log_file = None
            self._log_start_time = None
            self._log_bytes = 0
            return result
```

Also add `self._log_path: str | None = None` to `__init__` next to the other logging attributes.

Update the `close()` method to stop logging before shutting down (add before `self._stop_event.set()`):

```python
    def close(self) -> None:
        """Stops the reader thread and closes the serial port."""
        if self._log_file is not None:
            self.stop_logging()
        self._stop_event.set()
        self._data_event.set()
        self._reader_thread.join(timeout=2.0)
        if self._serial.is_open:
            self._serial.close()
        with self._lock:
            self._history.clear()
            self._read_cursor = 0
            self._buffer_bytes = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest tests/test_logging.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add serial_mcp/session.py tests/test_logging.py
git commit -m "feat: add log-to-file capture in SerialSession"
```

---

## Task 5: Logging — server.py tools

**Files:**
- Modify: `serial_mcp/server.py`

- [ ] **Step 1: Add serial_log_start and serial_log_stop tools to server.py**

Add after the `serial_clear_history` tool (around line 724), before the `serial_list_sessions` tool:

```python
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
```

- [ ] **Step 2: Run ruff**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/ruff check serial_mcp/server.py && .venv/bin/ruff format --check serial_mcp/server.py`
Expected: Clean.

- [ ] **Step 3: Run full test suite**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add serial_mcp/server.py
git commit -m "feat: add serial_log_start and serial_log_stop tools"
```

---

## Task 6: XMODEM — protocol implementation

**Files:**
- Create: `serial_mcp/xmodem.py`
- Create: `tests/test_xmodem.py`

- [ ] **Step 1: Write failing XMODEM tests**

Create `tests/test_xmodem.py`:

```python
import io

from serial_mcp.xmodem import xmodem_send, xmodem_receive, crc16_xmodem, XModemError

# Protocol constants
SOH = 0x01
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18


def test_crc16_xmodem():
    """CRC-16/XMODEM for known test vector."""
    assert crc16_xmodem(b"123456789") == 0x31C3


def test_crc16_empty():
    assert crc16_xmodem(b"") == 0x0000


def test_send_single_block_checksum():
    """Send a file smaller than 128 bytes using checksum mode."""
    data = b"Hello XMODEM" + b"\x1a" * (128 - 12)  # Padded to 128

    # Receiver sends NAK to initiate checksum mode, then ACK for block, then ACK for EOT
    receiver_responses = iter([bytes([NAK]), bytes([ACK]), bytes([ACK])])
    sent_data = bytearray()

    def read_fn(size, timeout=1.0):
        return next(receiver_responses)

    def write_fn(data):
        sent_data.extend(data)
        return len(data)

    result = xmodem_send(io.BytesIO(b"Hello XMODEM"), read_fn, write_fn, mode="xmodem")

    assert result["bytes_sent"] == 12
    assert result["block_count"] == 1
    # Verify SOH header
    assert sent_data[0] == SOH
    assert sent_data[1] == 1      # Block number
    assert sent_data[2] == 0xFE   # ~block number


def test_send_multi_block():
    """Send a file that spans multiple 128-byte blocks."""
    file_data = b"A" * 200  # 2 blocks needed (128 + 72 padded)

    responses = iter([
        bytes([NAK]),   # Initiate checksum mode
        bytes([ACK]),   # ACK block 1
        bytes([ACK]),   # ACK block 2
        bytes([ACK]),   # ACK EOT
    ])
    sent_data = bytearray()

    def read_fn(size, timeout=1.0):
        return next(responses)

    def write_fn(data):
        sent_data.extend(data)
        return len(data)

    result = xmodem_send(io.BytesIO(file_data), read_fn, write_fn, mode="xmodem")
    assert result["block_count"] == 2
    assert result["bytes_sent"] == 200


def test_send_crc_mode():
    """Send using CRC-16 mode (receiver sends 'C' to initiate)."""
    file_data = b"CRC test data"

    responses = iter([
        b"C",           # Initiate CRC mode
        bytes([ACK]),   # ACK block 1
        bytes([ACK]),   # ACK EOT
    ])
    sent_data = bytearray()

    def read_fn(size, timeout=1.0):
        return next(responses)

    def write_fn(data):
        sent_data.extend(data)
        return len(data)

    result = xmodem_send(io.BytesIO(file_data), read_fn, write_fn, mode="xmodem-crc")
    assert result["block_count"] == 1
    # CRC mode: SOH(1) + blk(1) + ~blk(1) + data(128) + crc(2) = 133
    # Find the first block in sent_data
    assert len(sent_data) >= 133


def test_receive_single_block():
    """Receive a single-block file."""
    payload = b"Received data" + b"\x1a" * (128 - 13)
    checksum = sum(payload) & 0xFF
    block = bytes([SOH, 1, 0xFE]) + payload + bytes([checksum])

    responses = iter([block, bytes([EOT])])
    sent_data = bytearray()

    def read_fn(size, timeout=1.0):
        resp = next(responses)
        return resp[:size] if size < len(resp) else resp

    def write_fn(data):
        sent_data.extend(data)
        return len(data)

    output = io.BytesIO()
    result = xmodem_receive(output, read_fn, write_fn, mode="xmodem")

    assert result["block_count"] == 1
    assert result["bytes_received"] > 0
    output.seek(0)
    content = output.read()
    assert content.startswith(b"Received data")


def test_send_retries_on_nak():
    """Sender should retry a block when receiver NAKs it."""
    file_data = b"retry test"

    responses = iter([
        bytes([NAK]),   # Initiate checksum mode
        bytes([NAK]),   # NAK block 1 (request retry)
        bytes([ACK]),   # ACK block 1 (retry succeeds)
        bytes([ACK]),   # ACK EOT
    ])
    sent_data = bytearray()

    def read_fn(size, timeout=1.0):
        return next(responses)

    def write_fn(data):
        sent_data.extend(data)
        return len(data)

    result = xmodem_send(io.BytesIO(file_data), read_fn, write_fn, mode="xmodem")
    assert result["block_count"] == 1
    # Block was sent twice (original + retry) + EOT
    block_size = 1 + 1 + 1 + 128 + 1  # SOH + blk + ~blk + data + checksum = 132
    eot_size = 1
    assert len(sent_data) == block_size * 2 + eot_size
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest tests/test_xmodem.py -v`
Expected: FAIL — `serial_mcp.xmodem` does not exist.

- [ ] **Step 3: Implement xmodem.py**

Create `serial_mcp/xmodem.py`:

```python
"""Pure-Python XMODEM implementation for serial file transfer.

Supports XMODEM (checksum) and XMODEM-CRC (CRC-16) modes.
Uses read/write callables for testability — no direct serial dependency.
"""

from __future__ import annotations

import io
import time
from typing import BinaryIO, Callable

# Protocol constants
SOH = 0x01   # Start of 128-byte block
EOT = 0x04   # End of transmission
ACK = 0x06   # Acknowledge
NAK = 0x15   # Negative acknowledge
CAN = 0x18   # Cancel
SUB = 0x1A   # Padding byte (Ctrl-Z)

BLOCK_SIZE = 128
MAX_RETRIES = 10


class XModemError(Exception):
    """XMODEM protocol error."""


def crc16_xmodem(data: bytes) -> int:
    """CRC-16/XMODEM (polynomial 0x1021, init 0x0000)."""
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


ReadFn = Callable[[int], bytes]     # read_fn(size, timeout) -> bytes
WriteFn = Callable[[bytes], int]    # write_fn(data) -> bytes_written


def xmodem_send(
    stream: BinaryIO,
    read_fn: ReadFn,
    write_fn: WriteFn,
    mode: str = "xmodem",
    timeout: float = 60.0,
) -> dict:
    """Send a file using XMODEM protocol.

    Args:
        stream: File-like object to read from.
        read_fn: Callable that reads bytes from the serial port.
        write_fn: Callable that writes bytes to the serial port.
        mode: "xmodem" (checksum) or "xmodem-crc" (CRC-16).
        timeout: Total transfer timeout in seconds.

    Returns:
        Dict with bytes_sent, block_count, duration.
    """
    start_time = time.time()
    use_crc = mode == "xmodem-crc"

    # Wait for receiver to initiate
    deadline = time.time() + timeout
    init_byte = None
    while time.time() < deadline:
        resp = read_fn(1)
        if resp:
            b = resp[0]
            if b == NAK and not use_crc:
                init_byte = NAK
                break
            elif b == ord("C") and use_crc:
                init_byte = ord("C")
                break
            elif b == NAK and use_crc:
                # Receiver doesn't support CRC, fall back to checksum
                use_crc = False
                init_byte = NAK
                break

    if init_byte is None:
        raise XModemError("Timeout waiting for receiver to initiate transfer")

    # Read entire file
    file_data = stream.read()
    total_bytes = len(file_data)

    # Pad to block boundary
    if len(file_data) % BLOCK_SIZE != 0:
        file_data += bytes([SUB]) * (BLOCK_SIZE - len(file_data) % BLOCK_SIZE)

    block_count = len(file_data) // BLOCK_SIZE
    block_num = 1

    for i in range(block_count):
        block_data = file_data[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE]

        for retry in range(MAX_RETRIES):
            if time.time() > deadline:
                raise XModemError("Transfer timeout")

            # Build packet
            header = bytes([SOH, block_num & 0xFF, (255 - block_num) & 0xFF])
            if use_crc:
                crc = crc16_xmodem(block_data)
                check = bytes([crc >> 8, crc & 0xFF])
            else:
                check = bytes([sum(block_data) & 0xFF])

            write_fn(header + block_data + check)

            # Wait for ACK/NAK
            resp = read_fn(1)
            if resp and resp[0] == ACK:
                break
            elif resp and resp[0] == CAN:
                raise XModemError("Transfer cancelled by receiver")
            # NAK or timeout — retry
        else:
            raise XModemError(f"Block {block_num} failed after {MAX_RETRIES} retries")

        block_num = (block_num + 1) & 0xFF

    # Send EOT
    for _ in range(MAX_RETRIES):
        write_fn(bytes([EOT]))
        resp = read_fn(1)
        if resp and resp[0] == ACK:
            break
    else:
        raise XModemError("EOT not acknowledged")

    return {
        "bytes_sent": total_bytes,
        "block_count": block_count,
        "duration": round(time.time() - start_time, 2),
    }


def xmodem_receive(
    stream: BinaryIO,
    read_fn: ReadFn,
    write_fn: WriteFn,
    mode: str = "xmodem",
    timeout: float = 60.0,
) -> dict:
    """Receive a file using XMODEM protocol.

    Args:
        stream: File-like object to write received data to.
        read_fn: Callable that reads bytes from the serial port.
        write_fn: Callable that writes bytes to the serial port.
        mode: "xmodem" (checksum) or "xmodem-crc" (CRC-16).
        timeout: Total transfer timeout in seconds.

    Returns:
        Dict with bytes_received, block_count, duration.
    """
    start_time = time.time()
    use_crc = mode == "xmodem-crc"
    deadline = time.time() + timeout

    # Initiate transfer
    if use_crc:
        write_fn(b"C")
    else:
        write_fn(bytes([NAK]))

    block_count = 0
    expected_block = 1
    total_bytes = 0

    while time.time() < deadline:
        # Read first byte (SOH or EOT)
        header = read_fn(1)
        if not header:
            continue

        if header[0] == EOT:
            write_fn(bytes([ACK]))
            break
        elif header[0] == CAN:
            raise XModemError("Transfer cancelled by sender")
        elif header[0] != SOH:
            write_fn(bytes([NAK]))
            continue

        # Read block: block_num(1) + ~block_num(1) + data(128) + check(1 or 2)
        check_size = 2 if use_crc else 1
        packet_size = 2 + BLOCK_SIZE + check_size
        packet = read_fn(packet_size)
        if len(packet) < packet_size:
            write_fn(bytes([NAK]))
            continue

        blk = packet[0]
        blk_comp = packet[1]
        block_data = packet[2 : 2 + BLOCK_SIZE]
        check_bytes = packet[2 + BLOCK_SIZE :]

        # Validate block number complement
        if (blk + blk_comp) & 0xFF != 0xFF:
            write_fn(bytes([NAK]))
            continue

        # Validate checksum/CRC
        if use_crc:
            expected_crc = crc16_xmodem(block_data)
            received_crc = (check_bytes[0] << 8) | check_bytes[1]
            if expected_crc != received_crc:
                write_fn(bytes([NAK]))
                continue
        else:
            expected_sum = sum(block_data) & 0xFF
            if expected_sum != check_bytes[0]:
                write_fn(bytes([NAK]))
                continue

        # Accept block
        if blk == expected_block & 0xFF:
            stream.write(block_data)
            total_bytes += BLOCK_SIZE
            expected_block = (expected_block + 1) & 0xFF
            block_count += 1

        write_fn(bytes([ACK]))
    else:
        raise XModemError("Receive timeout")

    return {
        "bytes_received": total_bytes,
        "block_count": block_count,
        "duration": round(time.time() - start_time, 2),
    }
```

- [ ] **Step 4: Run XMODEM tests**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest tests/test_xmodem.py -v`
Expected: All PASS (some tests may need minor adjustments to match the implementation's read_fn signature — fix as needed).

- [ ] **Step 5: Run full test suite and lint**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/ruff check serial_mcp/xmodem.py && .venv/bin/pytest -v`
Expected: All clean and passing.

- [ ] **Step 6: Commit**

```bash
git add serial_mcp/xmodem.py tests/test_xmodem.py
git commit -m "feat: add pure-Python XMODEM send/receive implementation"
```

---

## Task 7: XMODEM — session integration and server tools

**Files:**
- Modify: `serial_mcp/session.py`
- Modify: `serial_mcp/server.py`

- [ ] **Step 1: Add pause_reader/resume_reader to session.py**

Add after the `stop_logging` method (in the Logging section):

```python
    # ── Reader control ──────────────────────────────────────────────

    def pause_reader(self) -> None:
        """Pause the background reader thread. Used during XMODEM transfers."""
        self._stop_event.set()
        self._data_event.set()
        self._reader_thread.join(timeout=2.0)

    def resume_reader(self) -> None:
        """Resume the background reader thread after a pause."""
        self._stop_event.clear()
        self._data_event.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def raw_read(self, size: int, timeout: float = 1.0) -> bytes:
        """Read bytes directly from the serial port (bypassing the reader thread).

        Used during XMODEM transfers when the reader thread is paused.
        """
        self._serial.timeout = timeout
        data = self._serial.read(size)
        return data

    def raw_write(self, data: bytes) -> int:
        """Write bytes directly to the serial port."""
        return self._serial.write(data)
```

- [ ] **Step 2: Add XMODEM tools to server.py**

Add after the `serial_log_stop` tool:

```python
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
    from serial_mcp.xmodem import xmodem_send, XModemError

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
    from serial_mcp.xmodem import xmodem_receive, XModemError

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
```

- [ ] **Step 3: Run lint and tests**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/ruff check serial_mcp/ && .venv/bin/pytest -v`
Expected: All clean and passing.

- [ ] **Step 4: Commit**

```bash
git add serial_mcp/session.py serial_mcp/server.py
git commit -m "feat: add XMODEM file transfer tools with reader pause/resume"
```

---

## Task 8: Elicitation — port picker in serial_open

**Files:**
- Modify: `serial_mcp/server.py`

- [ ] **Step 1: Modify serial_open to accept optional port with elicitation**

Note: `Context` is already imported from `mcp.server.fastmcp`. The elicitation fallback uses a broad `except Exception` to handle both `CapabilityNotSupported` and SDKs that don't support elicitation at all — no additional imports needed.

Replace the `serial_open` function (lines 98-156) with:

```python
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
) -> dict:
    """Open a serial connection to the specified port.

    If port is omitted, lists available ports and asks you to pick one.

    Common configurations:
    - Most devices: 115200 baud, 8N1 (the defaults)
    - Older equipment: 9600 baud, 8N1
    - Use serial_detect_baud() first if unsure of the baud rate.

    Args:
        port: Serial port device path (e.g. /dev/ttyUSB0, COM3). If omitted, prompts for selection.
        baud_rate: Baud rate for the connection
        data_bits: Number of data bits (5, 6, 7, or 8)
        stop_bits: Number of stop bits (1, 1.5, or 2)
        parity: Parity checking ("none", "even", "odd", "mark", "space")
        timeout: Read timeout in seconds
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
                result = await ctx.elicit(
                    "Multiple serial ports found. Select one:",
                    response_type=ports,
                )
                if result.action == "accept" and result.data:
                    port = result.data
                else:
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
```

- [ ] **Step 2: Run lint and tests**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/ruff check serial_mcp/server.py && .venv/bin/pytest -v`
Expected: All clean and passing.

- [ ] **Step 3: Commit**

```bash
git add serial_mcp/server.py
git commit -m "feat: add port picker elicitation to serial_open"
```

---

## Task 9: Elicitation — baud rate confirmation in serial_detect_baud

**Files:**
- Modify: `serial_mcp/server.py`

- [ ] **Step 1: Add elicitation to serial_detect_baud**

The `serial_detect_baud` function already accepts `ctx: Context`. After the results are sorted (around line 686), add elicitation before the return:

Replace the return block (the final `return { ... }` of `serial_detect_baud`) with:

```python
    if not results:
        return {
            "port": port,
            "results": results,
            "recommended": None,
            "message": (
                "No data received at any baud rate. Check wiring and that "
                "the device is powered on."
            ),
        }

    # Try to elicit baud rate confirmation
    top_results = results[:3]
    choices = [
        f"{r['baud_rate']} ({int(r['readable_ratio'] * 100)}% readable)"
        for r in top_results
    ]

    selected_baud = results[0]["baud_rate"]
    try:
        elicit_result = await ctx.elicit(
            f"Baud rate detection complete for {port}. Confirm rate:",
            response_type=choices,
        )
        if elicit_result.action == "accept" and elicit_result.data:
            # Parse baud rate from the selection string (e.g., "115200 (98% readable)")
            selected_baud = int(elicit_result.data.split(" ")[0])
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
```

- [ ] **Step 2: Run lint and tests**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/ruff check serial_mcp/server.py && .venv/bin/pytest -v`
Expected: All clean and passing.

- [ ] **Step 3: Commit**

```bash
git add serial_mcp/server.py
git commit -m "feat: add baud rate confirmation elicitation to serial_detect_baud"
```

---

## Task 10: Server tool tests

**Files:**
- Create: `tests/test_server.py`

- [ ] **Step 1: Write server tool tests**

Create `tests/test_server.py`:

```python
import pytest

from serial_mcp.server import _resolve_session, _sessions


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure sessions dict is empty before/after each test."""
    _sessions.clear()
    yield
    _sessions.clear()


def test_resolve_session_no_sessions():
    """Should raise when no sessions are open."""
    with pytest.raises(RuntimeError, match="No sessions open"):
        _resolve_session()


def test_resolve_session_single(mock_serial):
    """Should auto-select when only one session is open."""
    from serial_mcp.session import SerialSession

    session = SerialSession(
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
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
        port="/dev/ttyTEST", baud_rate=115200, data_bits=8,
        stop_bits=1, parity="none", timeout=1.0,
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
```

- [ ] **Step 2: Run tests**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest tests/test_server.py -v`
Expected: All PASS.

- [ ] **Step 3: Run full suite**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_server.py
git commit -m "test: add server tool unit tests"
```

---

## Task 11: Version bump and ruff cleanup

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Bump version to 0.4.0 in pyproject.toml**

Change `version = "0.3.0"` to `version = "0.4.0"` in `pyproject.toml`.

- [ ] **Step 2: Update .gitignore**

Add to `.gitignore`:

```
build/
dist/
*.mcpb
```

- [ ] **Step 3: Run ruff format on all files**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/ruff format serial_mcp/ tests/ && .venv/bin/ruff check --fix serial_mcp/ tests/`

- [ ] **Step 4: Run full test suite**

Run: `cd /Volumes/Secondary/serial-mcp && .venv/bin/pytest -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore serial_mcp/ tests/
git commit -m "chore: bump version to 0.4.0, update .gitignore, format code"
```

---

## Task 12: MCPB — manifest and build script

**Files:**
- Create: `manifest.json`
- Create: `scripts/build-mcpb.sh`
- Create: `scripts/run.py`

- [ ] **Step 1: Create manifest.json**

Create `manifest.json` in project root:

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

- [ ] **Step 2: Create scripts/run.py**

Create `scripts/run.py`:

```python
"""MCPB entry script — adds vendored dependencies to sys.path."""
import os
import sys

# Add vendor directory to path
vendor_dir = os.path.join(os.path.dirname(__file__), "vendor")
if os.path.isdir(vendor_dir):
    sys.path.insert(0, vendor_dir)

from serial_mcp.server import main

main()
```

- [ ] **Step 3: Create scripts/build-mcpb.sh**

Create `scripts/build-mcpb.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build/mcpb"

echo "==> Cleaning build directory"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/server"

echo "==> Copying serial_mcp package"
cp -r "$PROJECT_DIR/serial_mcp" "$BUILD_DIR/server/"

echo "==> Copying entry script"
cp "$SCRIPT_DIR/run.py" "$BUILD_DIR/server/"

echo "==> Vendoring dependencies"
pip install -t "$BUILD_DIR/server/vendor" -r "$PROJECT_DIR/requirements.txt" --quiet

echo "==> Copying manifest"
cp "$PROJECT_DIR/manifest.json" "$BUILD_DIR/"

echo "==> Packing MCPB"
cd "$BUILD_DIR"
npx @anthropic-ai/mcpb pack

echo "==> Done. Bundle is in $BUILD_DIR/"
ls -la "$BUILD_DIR"/*.mcpb 2>/dev/null || echo "(no .mcpb file found — check mcpb pack output)"
```

- [ ] **Step 4: Make build script executable**

Run: `chmod +x /Volumes/Secondary/serial-mcp/scripts/build-mcpb.sh`

- [ ] **Step 5: Commit**

```bash
git add manifest.json scripts/run.py scripts/build-mcpb.sh
git commit -m "feat: add MCPB manifest and build script for bundled distribution"
```

---

## Task 13: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md to reflect new tools and build commands**

Add to the `Build & Run Commands` section:

```bash
# Run tests
pytest -v

# Lint
ruff check serial_mcp/ tests/
ruff format --check serial_mcp/ tests/

# Build MCPB bundle
./scripts/build-mcpb.sh
```

Add to the `Architecture` section, after the session.py bullet:

```
- **xmodem.py** — Pure-Python XMODEM send/receive. Takes read/write callables for testability. Supports checksum and CRC-16 modes.
```

Update the tool count from "~18 async tools" to "~22 async tools" and add mention of XMODEM and logging tools.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with new tools, tests, and build commands"
```

---

## Summary

| Task | What it does | New/modified files |
|------|-------------|-------------------|
| 1 | pyproject.toml config for ruff, pytest, dev deps | `pyproject.toml`, `tests/__init__.py` |
| 2 | GitHub Actions CI workflow | `.github/workflows/ci.yml` |
| 3 | Test fixtures + session unit tests | `tests/conftest.py`, `tests/test_session.py` |
| 4 | Log-to-file in session.py | `serial_mcp/session.py`, `tests/test_logging.py` |
| 5 | Log start/stop server tools | `serial_mcp/server.py` |
| 6 | XMODEM protocol implementation | `serial_mcp/xmodem.py`, `tests/test_xmodem.py` |
| 7 | XMODEM session integration + server tools | `serial_mcp/session.py`, `serial_mcp/server.py` |
| 8 | Port picker elicitation | `serial_mcp/server.py` |
| 9 | Baud confirmation elicitation | `serial_mcp/server.py` |
| 10 | Server tool unit tests | `tests/test_server.py` |
| 11 | Version bump + ruff cleanup | `pyproject.toml`, `.gitignore` |
| 12 | MCPB manifest + build script | `manifest.json`, `scripts/run.py`, `scripts/build-mcpb.sh` |
| 13 | Update CLAUDE.md | `CLAUDE.md` |
