#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_NAME="${LOCAL_MODEL_NAME:-local-ollama-model:latest}"
MODEL_DIR="${LOCAL_MODEL_DIR:-$ROOT_DIR/models/local-ollama-model}"
GGUF_FILE="${LOCAL_MODEL_FILE:-model.gguf}"
UPSTREAM_GGUF="${UPSTREAM_GGUF:-}"

DEFAULT_OLLAMA_BIN="$(command -v ollama || true)"
if [ -z "$DEFAULT_OLLAMA_BIN" ] && [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ]; then
  DEFAULT_OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"
fi
OLLAMA_BIN="${OLLAMA_BIN:-$DEFAULT_OLLAMA_BIN}"

if [ ! -x "$OLLAMA_BIN" ]; then
  echo "Cannot find Ollama."
  echo "Install Ollama or set OLLAMA_BIN to the ollama executable path."
  exit 1
fi

mkdir -p "$MODEL_DIR"

if [ ! -f "$MODEL_DIR/$GGUF_FILE" ]; then
  if [ -n "$UPSTREAM_GGUF" ] && [ -f "$UPSTREAM_GGUF" ]; then
    echo "Linking GGUF from upstream path..."
    ln -sf "$UPSTREAM_GGUF" "$MODEL_DIR/$GGUF_FILE"
  else
    echo "Missing GGUF model file:"
    echo "  $MODEL_DIR/$GGUF_FILE"
    echo
    echo "Put the model file there, or set UPSTREAM_GGUF to an existing GGUF path."
    exit 1
  fi
fi

"$OLLAMA_BIN" create "$MODEL_NAME" -f "$MODEL_DIR/Modelfile"

echo "Created $MODEL_NAME"
echo "Run EchoMind Python with: AI_PROVIDER=ollama ./scripts/run-dev.sh"
