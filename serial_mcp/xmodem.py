"""Pure-Python XMODEM implementation for serial file transfer.

Supports XMODEM (checksum) and XMODEM-CRC (CRC-16) modes.
Uses read/write callables for testability — no direct serial dependency.
"""

from __future__ import annotations

import time
from typing import BinaryIO, Callable

# Protocol constants
SOH = 0x01  # Start of 128-byte block
EOT = 0x04  # End of transmission
ACK = 0x06  # Acknowledge
NAK = 0x15  # Negative acknowledge
CAN = 0x18  # Cancel
SUB = 0x1A  # Padding byte (Ctrl-Z)

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


ReadFn = Callable[[int], bytes]
WriteFn = Callable[[bytes], int]


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
