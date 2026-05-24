import threading

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
        self._written_data = bytearray()
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
        with self._lock:
            self._written_data.extend(data)
        return len(data)

    @property
    def written_data(self) -> bytes:
        with self._lock:
            return bytes(self._written_data)

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
