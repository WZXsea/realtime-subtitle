#!/bin/bash

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# HuggingFace Mirror & Local Cache Fix
# This solves the "Operation not permitted" and slow download issues
export HF_ENDPOINT=https://hf-mirror.com
export HUGGINGFACE_HUB_CACHE="./.cache/huggingface/hub"
mkdir -p "$HUGGINGFACE_HUB_CACHE"

if [ ! -d ".venv" ]; then
    echo "[ERROR] Virtual environment not found."
    echo "Please run './install_mac.sh' first."
    exit 1
fi

echo "[Launcher] Activating environment..."
source .venv/bin/activate

echo "[Launcher] Starting App (Hot Reload Mode)..."
python reloader.py
