import threading
import time
from asyncio import IncompleteReadError

import pytest
import serialx


class MockSerial:
    """A mock serialx port that simulates a serial device with injectable data."""

    def __init__(self, url="/dev/ttyTEST", **kwargs):
        self.path = url
        self.baudrate = kwargs.get("baudrate", 115200)
        self.byte_size = kwargs.get("byte_size", 8)
        self.stopbits = kwargs.get("stopbits", serialx.StopBits.ONE)
        self.parity = kwargs.get("parity", serialx.Parity.NONE)
        self.read_timeout = kwargs.get("read_timeout", 1.0)

        self._is_open = False
        self._input_buffer = bytearray()
        self._written_data = bytearray()
        self._lock = threading.Lock()
        self._data = threading.Event()
        self.pins = serialx.ModemPins(dtr=serialx.PinState.HIGH, rts=serialx.PinState.HIGH)
        self.reconfigure_error: Exception | None = None

    @property
    def is_open(self):
        return self._is_open

    def open(self):
        self._is_open = True

    def readinto(self, b, *, timeout=None):
        if not self._data.wait(self.read_timeout if timeout is None else timeout):
            return 0
        with self._lock:
            n = min(len(b), len(self._input_buffer))
            b[:n] = self._input_buffer[:n]
            del self._input_buffer[:n]
            if not self._input_buffer:
                self._data.clear()
            return n

    def readexactly(self, n, *, timeout=None):
        deadline = time.monotonic() + (self.read_timeout if timeout is None else timeout)
        out = bytearray()
        buf = bytearray(n)
        while len(out) < n:
            got = self.readinto(memoryview(buf)[: n - len(out)], timeout=max(0, deadline - time.monotonic()))
            if not got:
                raise IncompleteReadError(bytes(out), n)
            out += buf[:got]
        return bytes(out)

    def write(self, data):
        with self._lock:
            self._written_data.extend(data)
        return len(data)

    def get_modem_pins(self):
        return self.pins

    def set_modem_pins(self, modem_pins=None, *, dtr=serialx.PinState.UNDEFINED, rts=serialx.PinState.UNDEFINED):
        update = {}
        for name, value in (("dtr", dtr), ("rts", rts)):
            state = serialx.PinState.convert(value)
            if state is not serialx.PinState.UNDEFINED:
                update[name] = state
        self.pins = serialx.ModemPins(**{**{"dtr": self.pins.dtr, "rts": self.pins.rts}, **update})

    def reconfigure_port(self, **kwargs):
        # Real serialx re-applies the on-open pin state (DTR/RTS high) and stores settings first.
        self.pins = serialx.ModemPins(dtr=serialx.PinState.HIGH, rts=serialx.PinState.HIGH)
        for k, v in kwargs.items():
            setattr(self, k, v)
        if self.reconfigure_error is not None:
            err, self.reconfigure_error = self.reconfigure_error, None
            raise err

    def reset_read_buffer(self):
        with self._lock:
            self._input_buffer.clear()
            self._data.clear()

    @property
    def written_data(self) -> bytes:
        with self._lock:
            return bytes(self._written_data)

    def close(self):
        self._is_open = False

    def inject_data(self, data: bytes):
        """Test helper: simulate data arriving on the serial port."""
        with self._lock:
            self._input_buffer.extend(data)
            self._data.set()


@pytest.fixture
def mock_serial(monkeypatch):
    """Patches serialx.serial_for_url to return a MockSerial instance.

    Returns the MockSerial instance so tests can inject data.
    """
    instance = MockSerial()

    def fake_serial_for_url(url, **kwargs):
        instance.path = url
        instance.baudrate = kwargs.get("baudrate", instance.baudrate)
        return instance

    monkeypatch.setattr("serialx.serial_for_url", fake_serial_for_url)
    return instance
