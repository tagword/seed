#!/usr/bin/env bash
set -euo pipefail

SEED_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "📦 Installing Seed components (editable mode)..."
pip install -e "$SEED_ROOT/../seed-engine"
pip install -e "$SEED_ROOT/../seed-services"
pip install -e "$SEED_ROOT/../seed-tools"
pip install -e "$SEED_ROOT"

echo ""
echo "✅ Done! Run 'seed info' to verify."
