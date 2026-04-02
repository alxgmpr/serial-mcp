import threading
import time

from serial_mcp.session import SerialSession


def test_read_buffer_returns_injected_data(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        time.sleep(0.05)
        mock_serial.inject_data(b"hello world")
        time.sleep(0.05)
        result = session.read_buffer(timeout=1.0)
        assert result["data"] == "hello world"
        assert result["byte_count"] == 11
    finally:
        session.close()


def test_read_buffer_empty_timeout(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        result = session.read_buffer(timeout=0.1)
        assert result["data"] == ""
        assert result["byte_count"] == 0
    finally:
        session.close()


def test_read_cursor_advances(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        time.sleep(0.05)
        mock_serial.inject_data(b"hello")
        time.sleep(0.05)
        result_since = session.read_since()
        assert result_since["data"] == "hello"
        result_buf = session.read_buffer(timeout=1.0)
        assert result_buf["data"] == "hello"
    finally:
        session.close()


def test_history_trimming(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
        max_history_bytes=100,
    )
    try:
        time.sleep(0.05)
        for i in range(20):
            mock_serial.inject_data(b"x" * 10)
            time.sleep(0.02)
        time.sleep(0.1)
        assert session._buffer_bytes <= 100
        assert session._total_bytes_received == 200
    finally:
        session.close()


def test_pattern_matching(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        time.sleep(0.05)

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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
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
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        assert session.is_healthy is True
        assert session.health_status == {"healthy": True}
    finally:
        session.close()


def test_close_clears_state(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    time.sleep(0.05)
    mock_serial.inject_data(b"data")
    time.sleep(0.05)
    session.close()
    assert mock_serial.is_open is False
