#!/usr/bin/env bash
# Deploy script — run on the server after git pull
# Usage: bash scripts/deploy.sh
set -o errexit
cd "$(dirname "$0")/.."

venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
sudo systemctl restart clips
