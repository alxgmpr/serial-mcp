import os
import tempfile
import time

from serial_mcp.session import SerialSession


def test_start_logging_creates_file(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name
        time.sleep(0.05)
        session.start_logging(log_path)
        mock_serial.inject_data(b"hello from device\r\n")
        time.sleep(0.1)
        session.stop_logging()
        with open(log_path) as f:
            content = f.read()
        assert "hello from device" in content
        assert "[" in content  # Timestamp bracket
    finally:
        session.close()
        os.unlink(log_path)


def test_stop_logging_returns_stats(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        log_path = f.name
    try:
        session.start_logging(log_path)
        session.close()
        assert session.is_logging is False
    finally:
        os.unlink(log_path)
