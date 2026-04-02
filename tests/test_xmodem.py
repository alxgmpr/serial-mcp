import io

from serial_mcp.xmodem import (
    crc16_xmodem,
    xmodem_receive,
    xmodem_send,
)

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
    responses = iter([bytes([NAK]), bytes([ACK]), bytes([ACK])])
    sent_data = bytearray()

    def read_fn(size):
        return next(responses)

    def write_fn(data):
        sent_data.extend(data)
        return len(data)

    result = xmodem_send(io.BytesIO(b"Hello XMODEM"), read_fn, write_fn, mode="xmodem")

    assert result["bytes_sent"] == 12
    assert result["block_count"] == 1
    assert sent_data[0] == SOH
    assert sent_data[1] == 1  # Block number
    assert sent_data[2] == 0xFE  # ~block number


def test_send_multi_block():
    """Send a file that spans multiple 128-byte blocks."""
    file_data = b"A" * 200

    responses = iter(
        [
            bytes([NAK]),  # Initiate checksum mode
            bytes([ACK]),  # ACK block 1
            bytes([ACK]),  # ACK block 2
            bytes([ACK]),  # ACK EOT
        ]
    )
    sent_data = bytearray()

    def read_fn(size):
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

    responses = iter(
        [
            b"C",  # Initiate CRC mode
            bytes([ACK]),  # ACK block 1
            bytes([ACK]),  # ACK EOT
        ]
    )
    sent_data = bytearray()

    def read_fn(size):
        return next(responses)

    def write_fn(data):
        sent_data.extend(data)
        return len(data)

    result = xmodem_send(io.BytesIO(file_data), read_fn, write_fn, mode="xmodem-crc")
    assert result["block_count"] == 1
    # CRC mode: SOH(1) + blk(1) + ~blk(1) + data(128) + crc(2) = 133
    assert len(sent_data) >= 133


def test_receive_single_block():
    """Receive a single-block file."""
    payload = b"Received data" + b"\x1a" * (128 - 13)
    checksum = sum(payload) & 0xFF
    # The implementation reads header (1 byte) then packet (2 + 128 + 1 = 131 bytes)
    header_byte = bytes([SOH])
    packet_body = bytes([1, 0xFE]) + payload + bytes([checksum])
    eot_byte = bytes([EOT])

    responses = iter([header_byte, packet_body, eot_byte])
    sent_data = bytearray()

    def read_fn(size):
        return next(responses)

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

    responses = iter(
        [
            bytes([NAK]),  # Initiate checksum mode
            bytes([NAK]),  # NAK block 1 (request retry)
            bytes([ACK]),  # ACK block 1 (retry succeeds)
            bytes([ACK]),  # ACK EOT
        ]
    )
    sent_data = bytearray()

    def read_fn(size):
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
