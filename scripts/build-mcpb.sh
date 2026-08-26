#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build/mcpb"
MCPB_VERSION="2.1.2"

echo "==> Cleaning build directory"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/server" "$BUILD_DIR/serial_mcp"

echo "==> Copying serial_mcp package"
cp "$PROJECT_DIR"/serial_mcp/*.py "$BUILD_DIR/serial_mcp/"

echo "==> Copying entry script"
cp "$SCRIPT_DIR/run.py" "$BUILD_DIR/server/"

echo "==> Copying uv project files"
cp "$PROJECT_DIR/pyproject.toml" "$PROJECT_DIR/uv.lock" "$PROJECT_DIR/README.md" "$PROJECT_DIR/LICENSE" "$BUILD_DIR/"

echo "==> Copying manifest"
cp "$PROJECT_DIR/manifest.json" "$BUILD_DIR/"

echo "==> Packing MCPB"
cd "$BUILD_DIR"
npx --yes "@anthropic-ai/mcpb@$MCPB_VERSION" pack

NAME=$(python3 -c "import json; print(json.load(open('manifest.json'))['name'])")
VERSION=$(python3 -c "import json; print(json.load(open('manifest.json'))['version'])")
TARGET="${NAME}-${VERSION}.mcpb"
if [ -f "$BUILD_DIR/mcpb.mcpb" ] && [ "$TARGET" != "mcpb.mcpb" ]; then
    mv "$BUILD_DIR/mcpb.mcpb" "$BUILD_DIR/$TARGET"
fi

echo "==> Done. Bundle is in $BUILD_DIR/"
ls -la "$BUILD_DIR"/*.mcpb 2>/dev/null || echo "(no .mcpb file found — check mcpb pack output)"
