# serial-mcp

Give an LLM a reliable serial connection to microcontrollers, routers, modems,
embedded Linux systems, and anything else with a UART.

`serial-mcp` is an [MCP](https://modelcontextprotocol.io/) server built for real
device work: interactive shells, bootloader prompts, binary protocols, logging,
and file transfers. It continuously buffers incoming data so output is not lost
between tool calls.

![Claude using serial-mcp to inspect a connected device](https://github.com/user-attachments/assets/17e948ae-4888-4748-8694-77c1e257e329)

## Install

The PyPI package is named `pyserial-mcp`; the command it installs is
`serial-mcp`. Python 3.10 or newer is required.

For a persistent installation with explicit upgrades:

```sh
uv tool install pyserial-mcp
```

Or with pip:

```sh
pip install pyserial-mcp
```

Upgrade an existing uv installation with `uv tool upgrade pyserial-mcp`.

## Connect it to your MCP client

For Codex (the desktop app, CLI, and IDE extension share this configuration):

```sh
codex mcp add serial-mcp -- serial-mcp
```

For Claude Code:

```sh
claude mcp add --scope user serial-mcp -- serial-mcp
```

The quickest setup skips the separate installation and lets `uvx` download and
run the package. Use the command for your client:

```sh
codex mcp add serial-mcp -- uvx pyserial-mcp
claude mcp add --scope user serial-mcp -- uvx pyserial-mcp
```

For clients that use an MCP JSON configuration:

```json
{
  "mcpServers": {
    "serial": {
      "command": "uvx",
      "args": ["pyserial-mcp"]
    }
  }
}
```

## What it can do

- Discover serial ports and USB metadata, then detect an unknown baud rate.
- Run a single command with automatic open/close, or keep a session open for an
  interactive shell.
- Read and write text, raw bytes, and hex data without losing output between
  calls.
- React immediately to boot prompts with regex-triggered text or binary replies.
- Control DTR, RTS, break, and read CTS, DSR, RI, and CD signals.
- Capture logs and send or receive files with XMODEM checksum or CRC-16.
- Identify the process holding a busy port and, with explicit use of
  `serial_force_release`, terminate it.

Open sessions automatically close after 15 minutes of inactivity by default.
Clients should still call `serial_close` as soon as a session is finished so the
port is available to other programs.

## Typical workflows

Ask your LLM naturally, for example:

> Find the connected serial device, detect its baud rate, and show me its shell
> prompt.

For one command, the server provides a safe one-shot tool:

```text
serial_execute(port="/dev/ttyUSB0", data="uname -a", expect="\\$")
```

For longer work, use `serial_open`, one or more `serial_command` calls, and
`serial_close`. Use `serial_wait_for(..., respond=" ")` to catch a time-sensitive
prompt such as U-Boot's autoboot interruption.

## Tool profiles

The default `full` profile exposes all 24 tools. If your client loads every tool
schema and you only need common text workflows, use the smaller seven-tool
profile:

```sh
serial-mcp --profile core
```

You can also set `SERIAL_MCP_TOOL_PROFILE=core` in the server environment.

## Development

No hardware is required to run the test suite:

```sh
git clone https://github.com/alxgmpr/serial-mcp.git
cd serial-mcp
uv pip install -e ".[dev]"
pytest -v
```

## License

[MIT](LICENSE)
