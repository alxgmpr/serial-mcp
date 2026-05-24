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


# ── Activity tracking tests ─────────────────────────────────────────


def test_activity_tracking_init(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        assert session.last_activity > 0
        assert session.inactivity_seconds < 1.0
    finally:
        session.close()


def test_activity_tracking_write(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        initial = session.last_activity
        time.sleep(0.05)
        session.write(b"test")
        assert session.last_activity > initial
    finally:
        session.close()


def test_activity_tracking_read(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        initial = session.last_activity
        time.sleep(0.05)
        session.read_buffer(timeout=0.01)
        assert session.last_activity > initial
    finally:
        session.close()


def test_wait_for_with_on_match_send(mock_serial):
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
            mock_serial.inject_data(b"Hit any key to stop autoboot: 3")

        t = threading.Thread(target=delayed_inject)
        t.start()
        result = session.wait_for(r"Hit any key", timeout=2.0, on_match_send=b" ")
        assert result["timed_out"] is False
        assert result["responded"] is True
        assert result["response_bytes_sent"] == 1
        assert b" " in mock_serial.written_data
        t.join()
    finally:
        session.close()


def test_wait_for_on_match_send_timeout(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        result = session.wait_for(r"never_matches", timeout=0.2, on_match_send=b"\x03")
        assert result["timed_out"] is True
        assert result["responded"] is False
        assert result["response_bytes_sent"] == 0
        assert mock_serial.written_data == b""
    finally:
        session.close()


def test_command_with_expect_and_on_match_send(mock_serial):
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
            mock_serial.inject_data(b"Rebooting...\r\nHit any key to stop autoboot: 3")

        t = threading.Thread(target=delayed_response)
        t.start()
        result = session.command(b"reboot\r\n", expect="Hit any key", timeout=2.0, on_match_send=b" ")
        assert result["timed_out"] is False
        assert result["responded"] is True
        assert result["response_bytes_sent"] == 1
        written = mock_serial.written_data
        assert b"reboot\r\n" in written
        assert b" " in written
        t.join()
    finally:
        session.close()


def test_wait_for_without_on_match_send_no_respond_fields(mock_serial):
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
            mock_serial.inject_data(b"hello world")

        t = threading.Thread(target=delayed_inject)
        t.start()
        result = session.wait_for(r"hello", timeout=2.0)
        assert result["timed_out"] is False
        assert "responded" not in result
        assert "response_bytes_sent" not in result
        t.join()
    finally:
        session.close()


def test_wait_for_on_match_send_multi_byte(mock_serial):
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
            mock_serial.inject_data(b"Bootloader v2.0 ready")

        t = threading.Thread(target=delayed_inject)
        t.start()
        result = session.wait_for(
            r"Bootloader v", timeout=2.0, on_match_send=b"\x7f\xaa\x55"
        )
        assert result["timed_out"] is False
        assert result["responded"] is True
        assert result["response_bytes_sent"] == 3
        assert mock_serial.written_data == b"\x7f\xaa\x55"
        t.join()
    finally:
        session.close()


def test_wait_for_timeout_without_on_match_send_no_respond_fields(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        result = session.wait_for(r"never_matches", timeout=0.1)
        assert result["timed_out"] is True
        assert "responded" not in result
        assert "response_bytes_sent" not in result
    finally:
        session.close()


def test_activity_tracking_data_receipt(mock_serial):
    session = SerialSession(
        port="/dev/ttyTEST",
        baud_rate=115200,
        data_bits=8,
        stop_bits=1,
        parity="none",
        timeout=1.0,
    )
    try:
        initial = session.last_activity
        time.sleep(0.05)
        mock_serial.inject_data(b"incoming")
        time.sleep(0.05)
        assert session.last_activity > initial
    finally:
        session.close()
