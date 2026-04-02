"""MCPB entry script — adds vendored dependencies to sys.path."""
import os
import sys

# Add vendor directory to path
vendor_dir = os.path.join(os.path.dirname(__file__), "vendor")
if os.path.isdir(vendor_dir):
    sys.path.insert(0, vendor_dir)

from serial_mcp.server import main

main()
