import re
import threading
import time

import serial

_PARITY_MAP = {
    "none": serial.PARITY_NONE,
    "even": serial.PARITY_EVEN,
    "odd": serial.PARITY_ODD,
    "mark": serial.PARITY_MARK,
    "space": serial.PARITY_SPACE,
}
_STOPBITS_MAP = {
    1: serial.STOPBITS_ONE,
    1.5: serial.STOPBITS_ONE_POINT_FIVE,
    2: serial.STOPBITS_TWO,
}


class SerialSession:
    """Manages a single serial port connection with a background reader thread."""

    def __init__(
        self,
        port: str,
        baud_rate: int,
        data_bits: int,
        stop_bits: float,
        parity: str,
        timeout: float,
        max_history_bytes: int = 10_000_000,
    ):
        self._serial = serial.Serial(
            port=port,
            baudrate=baud_rate,
            bytesize=data_bits,
            stopbits=_STOPBITS_MAP[stop_bits],
            parity=_PARITY_MAP[parity],
            timeout=timeout,
        )

        self.port = port
        self.baud_rate = baud_rate
        self.data_bits = data_bits
        self.stop_bits = stop_bits
        self.parity = parity
        self.connected_at = time.time()
        self._last_activity = time.time()

        self._history: list[tuple[float, bytes]] = []
        self._read_cursor: int = 0
        self._lock = threading.Lock()
        self._data_event = threading.Event()
        self._stop_event = threading.Event()

        # History management
        self._max_history_bytes = max_history_bytes
        self._buffer_bytes = 0
        self._total_bytes_received = 0

        # Connection health
        self._disconnected = False
        self._disconnect_reason: str | None = None

        # Logging
        self._log_file = None
        self._log_path: str | None = None
        self._log_start_time: float | None = None
        self._log_bytes = 0

        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    # ── Background reader ────────────────────────────────────────────

    def _reader_loop(self) -> None:
        """Continuously reads from the serial port into the history buffer."""
        while not self._stop_event.is_set():
            try:
                if self._serial.in_waiting > 0:
                    data = self._serial.read(self._serial.in_waiting)
                    if data:
                        with self._lock:
                            now = time.time()
                            self._history.append((now, data))
                            self._buffer_bytes += len(data)
                            self._total_bytes_received += len(data)
                            self._last_activity = now
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
                else:
                    time.sleep(0.01)
            except serial.SerialException as e:
                self._disconnected = True
                self._disconnect_reason = str(e)
                break

    def _trim_history(self) -> None:
        """Remove oldest entries to stay under max_history_bytes. Lock must be held."""
        if self._buffer_bytes <= self._max_history_bytes:
            return
        trim_count = 0
        freed = 0
        for _, chunk in self._history:
            if self._buffer_bytes - freed <= self._max_history_bytes:
                break
            freed += len(chunk)
            trim_count += 1
        if trim_count > 0:
            del self._history[:trim_count]
            self._buffer_bytes -= freed
            self._read_cursor = max(0, self._read_cursor - trim_count)

    # ── Read operations ──────────────────────────────────────────────

    def read_buffer(self, timeout: float = 1.0, encoding: str = "utf-8") -> dict:
        """Returns unread data since the last read, advancing the cursor.

        If no new data is available, waits up to timeout seconds for data to arrive.
        """
        self._last_activity = time.time()
        with self._lock:
            has_data = self._read_cursor < len(self._history)

        if not has_data:
            self._data_event.clear()
            self._data_event.wait(timeout=timeout)

        with self._lock:
            new_chunks = self._history[self._read_cursor :]
            self._read_cursor = len(self._history)

        data = b"".join(chunk for _, chunk in new_chunks)
        return {
            "data": data.decode(encoding, errors="replace"),
            "byte_count": len(data),
        }

    def read_buffer_hex(self, timeout: float = 1.0) -> dict:
        """Like read_buffer but returns data as hex string. For binary protocols."""
        with self._lock:
            has_data = self._read_cursor < len(self._history)

        if not has_data:
            self._data_event.clear()
            self._data_event.wait(timeout=timeout)

        with self._lock:
            new_chunks = self._history[self._read_cursor :]
            self._read_cursor = len(self._history)

        data = b"".join(chunk for _, chunk in new_chunks)
        return {
            "hex": data.hex(" ") if data else "",
            "byte_count": len(data),
        }

    def read_since(self, since: float | None = None, encoding: str = "utf-8") -> dict:
        """Return history data since a given timestamp (non-destructive).

        Does NOT advance the read cursor — independent of read_buffer().

        Args:
            since: Unix timestamp. If None, returns all data since session start.
            encoding: Character encoding for decoding.
        """
        self._last_activity = time.time()
        with self._lock:
            if since is None:
                chunks = list(self._history)
            else:
                chunks = [(ts, data) for ts, data in self._history if ts >= since]

        if not chunks:
            return {
                "data": "",
                "byte_count": 0,
                "chunk_count": 0,
                "time_range": None,
            }

        combined = b"".join(data for _, data in chunks)
        return {
            "data": combined.decode(encoding, errors="replace"),
            "byte_count": len(combined),
            "chunk_count": len(chunks),
            "time_range": {
                "earliest": chunks[0][0],
                "latest": chunks[-1][0],
            },
        }

    # ── Command / expect operations ──────────────────────────────────

    def command(
        self,
        data: bytes,
        expect: str | None = None,
        timeout: float = 5.0,
        encoding: str = "utf-8",
        settle_time: float = 0.3,
        on_match_send: bytes | None = None,
    ) -> dict:
        """Send data and wait for the response.

        If `expect` is a regex pattern, waits until it matches in the response.
        Otherwise waits until the device stops sending (settle_time of silence).
        """
        self._last_activity = time.time()
        with self._lock:
            start_cursor = len(self._history)

        self._serial.write(data)

        if expect:
            return self._wait_for_pattern(expect, timeout, encoding, start_cursor, on_match_send=on_match_send)

        # No expect: wait for data, then wait for silence
        deadline = time.time() + timeout
        last_history_len = start_cursor
        last_change_time: float | None = None

        while True:
            now = time.time()
            if now >= deadline:
                break

            with self._lock:
                current_len = len(self._history)

            if current_len > last_history_len:
                last_history_len = current_len
                last_change_time = now
            elif last_change_time is not None and (now - last_change_time) >= settle_time:
                break

            time.sleep(0.01)

        with self._lock:
            chunks = self._history[start_cursor:]
            self._read_cursor = len(self._history)

        combined = b"".join(chunk for _, chunk in chunks)
        return {
            "data": combined.decode(encoding, errors="replace"),
            "byte_count": len(combined),
            "timed_out": last_change_time is None,
        }

    def wait_for(
        self,
        pattern: str,
        timeout: float = 10.0,
        encoding: str = "utf-8",
        on_match_send: bytes | None = None,
    ) -> dict:
        """Wait for a regex pattern to appear in incoming data."""
        self._last_activity = time.time()
        with self._lock:
            start_cursor = len(self._history)
        return self._wait_for_pattern(pattern, timeout, encoding, start_cursor, on_match_send=on_match_send)

    def _wait_for_pattern(
        self,
        pattern: str,
        timeout: float,
        encoding: str,
        start_cursor: int,
        on_match_send: bytes | None = None,
    ) -> dict:
        """Wait for a regex pattern to appear in data received after start_cursor.

        If on_match_send is provided, those bytes are written to the serial port
        immediately when the pattern matches — before returning. This enables
        sub-millisecond triggered responses for time-sensitive sequences like
        bootloader autoboot interrupts.
        """
        compiled = re.compile(pattern)
        deadline = time.time() + timeout

        while time.time() < deadline:
            with self._lock:
                chunks = self._history[start_cursor:]

            combined = b"".join(chunk for _, chunk in chunks)
            text = combined.decode(encoding, errors="replace")

            match = compiled.search(text)
            if match:
                with self._lock:
                    self._read_cursor = len(self._history)
                result = {
                    "data": text,
                    "matched": match.group(),
                    "byte_count": len(combined),
                    "timed_out": False,
                }
                if on_match_send is not None:
                    result["response_bytes_sent"] = self._serial.write(on_match_send)
                    result["responded"] = True
                return result

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self._data_event.clear()
            self._data_event.wait(timeout=min(remaining, 0.1))

        # Timed out — return what we collected
        with self._lock:
            chunks = self._history[start_cursor:]
            self._read_cursor = len(self._history)

        combined = b"".join(chunk for _, chunk in chunks)
        result = {
            "data": combined.decode(encoding, errors="replace"),
            "matched": None,
            "byte_count": len(combined),
            "timed_out": True,
        }
        if on_match_send is not None:
            result["responded"] = False
            result["response_bytes_sent"] = 0
        return result

    # ── Write operations ─────────────────────────────────────────────

    def write(self, data: bytes) -> int:
        """Writes bytes to the serial port. Returns number of bytes written."""
        self._last_activity = time.time()
        return self._serial.write(data)

    # ── Hardware signal control ──────────────────────────────────────

    def set_signals(self, dtr: bool | None = None, rts: bool | None = None) -> dict:
        """Set DTR/RTS signals. Returns the state of all control signals."""
        if dtr is not None:
            self._serial.dtr = dtr
        if rts is not None:
            self._serial.rts = rts
        return self.get_signals()

    def get_signals(self) -> dict:
        """Read the current state of all serial control signals."""
        return {
            "dtr": self._serial.dtr,
            "rts": self._serial.rts,
            "cts": self._serial.cts,
            "dsr": self._serial.dsr,
            "ri": self._serial.ri,
            "cd": self._serial.cd,
        }

    def send_break(self, duration: float = 0.25) -> None:
        """Send a serial break signal for the given duration (seconds)."""
        self._serial.send_break(duration)

    # ── Session management ───────────────────────────────────────────

    def clear_history(self) -> None:
        """Clear the receive history buffer and reset the read cursor."""
        with self._lock:
            self._history.clear()
            self._read_cursor = 0
            self._buffer_bytes = 0

    def change_settings(self, **kwargs) -> None:
        """Change serial port settings without closing the connection.

        Supported kwargs: baud_rate, data_bits, stop_bits, parity.
        """
        if "baud_rate" in kwargs:
            self._serial.baudrate = kwargs["baud_rate"]
            self.baud_rate = kwargs["baud_rate"]
        if "data_bits" in kwargs:
            self._serial.bytesize = kwargs["data_bits"]
            self.data_bits = kwargs["data_bits"]
        if "stop_bits" in kwargs:
            self._serial.stopbits = _STOPBITS_MAP[kwargs["stop_bits"]]
            self.stop_bits = kwargs["stop_bits"]
        if "parity" in kwargs:
            self._serial.parity = _PARITY_MAP[kwargs["parity"]]
            self.parity = kwargs["parity"]

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

    # ── Properties ───────────────────────────────────────────────────

    @property
    def bytes_in_buffer(self) -> int:
        """Unread bytes (not yet consumed by read_buffer)."""
        with self._lock:
            return sum(len(chunk) for _, chunk in self._history[self._read_cursor :])

    @property
    def total_bytes_received(self) -> int:
        """Total bytes received since session start (including trimmed history)."""
        return self._total_bytes_received

    @property
    def is_open(self) -> bool:
        return self._serial.is_open

    @property
    def is_healthy(self) -> bool:
        """True if the port is open and the reader thread hasn't hit an error."""
        return self._serial.is_open and not self._disconnected

    @property
    def health_status(self) -> dict:
        """Detailed health info including disconnect reason if applicable."""
        if self._disconnected:
            return {"healthy": False, "reason": self._disconnect_reason or "Device disconnected"}
        if not self._serial.is_open:
            return {"healthy": False, "reason": "Port closed"}
        return {"healthy": True}

    @property
    def last_activity(self) -> float:
        return self._last_activity

    @property
    def inactivity_seconds(self) -> float:
        return time.time() - self._last_activity

    @property
    def uptime(self) -> float:
        return time.time() - self.connected_at

    # ── Cleanup ──────────────────────────────────────────────────────

    def close(self) -> None:
        """Stops the reader thread and closes the serial port."""
        if self._log_file is not None:
            self.stop_logging()
        self._stop_event.set()
        self._data_event.set()  # unblock any waiting read_buffer() call
        self._reader_thread.join(timeout=2.0)
        if self._serial.is_open:
            self._serial.close()
        with self._lock:
            self._history.clear()
            self._read_cursor = 0
            self._buffer_bytes = 0
