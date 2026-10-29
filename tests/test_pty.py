"""Integration tests against a real pseudo-terminal, exercising serialx without mocks."""

import os
import sys
import time

import pytest

if sys.platform == "win32":
    pytest.skip("pty tests are POSIX-only", allow_module_level=True)

import serialx  # noqa: E402

from serial_mcp.server import _test_baud_rate  # noqa: E402
from serial_mcp.session import SerialSession  # noqa: E402


@pytest.fixture
def pty_pair():
    master, slave = os.openpty()
    path = os.ttyname(slave)
    yield master, path
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def _open(path):
    return SerialSession(port=path, baud_rate=115200, data_bits=8, stop_bits=1, parity="none", timeout=1.0)


def test_pty_round_trip_and_raw_read(pty_pair):
    master, path = pty_pair
    session = _open(path)
    try:
        session.write(b"ping")
        assert os.read(master, 16) == b"ping"

        os.write(master, b"pong\x00\xff")
        assert session.wait_for("pong", timeout=2)["timed_out"] is False

        session.pause_reader()
        os.write(master, b"abc")
        assert session.raw_read(10, timeout=0.2) == b"abc"
        session.resume_reader()
    finally:
        session.close()


def test_pty_rejected_setting_rolls_back(pty_pair):
    _, path = pty_pair
    session = _open(path)
    try:
        with pytest.raises(serialx.UnsupportedSetting):
            session.change_settings(stop_bits=1.5)
        assert session.stop_bits == 1
        session.change_settings(baud_rate=9600)
        assert session.baud_rate == 9600
    finally:
        session.close()


def test_pty_second_open_is_busy(pty_pair):
    _, path = pty_pair
    session = _open(path)
    try:
        with pytest.raises(OSError, match="locked"):
            _open(path)
        with pytest.raises(RuntimeError, match="in use"):
            _test_baud_rate(path, 115200, probe=False)
    finally:
        session.close()


def test_pty_send_break_and_signals(pty_pair):
    _, path = pty_pair
    session = _open(path)
    try:
        session.send_break(0.01)
        assert set(session.get_signals()) == {"dtr", "rts", "cts", "dsr", "ri", "cd"}
    finally:
        session.close()


def test_pty_hangup_marks_unhealthy(pty_pair):
    master, path = pty_pair
    session = _open(path)
    try:
        os.close(master)
        deadline = time.monotonic() + 2
        while session.is_healthy and time.monotonic() < deadline:
            time.sleep(0.05)
        assert session.is_healthy is False
    finally:
        session.close()
