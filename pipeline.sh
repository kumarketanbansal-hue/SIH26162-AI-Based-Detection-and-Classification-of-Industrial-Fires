#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# SIH 2026 - Automated End-to-End Pipeline
# ---------------------------------------------------------------------------

echo "=========================================="
echo " Starting ML Data Pipeline: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# 1. Activate Python virtual environment
VENV_PATH="$HOME/sih_venv/bin/activate"
if [ -f "$VENV_PATH" ]; then
    echo "[INFO] Activating virtual environment at $VENV_PATH..."
    source "$VENV_PATH"
else
    echo "[WARNING] Virtual environment not found at $VENV_PATH. Using system/current Python environment."
fi

# 2. Change directory to project root (where this script resides)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[INFO] Working directory: $SCRIPT_DIR"
cd "$SCRIPT_DIR"

# 3. Step 1: FIRMS Ingestion
echo ""
echo "=== Step 1: FIRMS Ingestion ==="
python3 fetch_firms.py

# 4. Step 2: Feature Engineering
echo ""
echo "=== Step 2: Feature Engineering ==="
python3 build_features.py

# 5. Step 3: Train Classifier
echo ""
echo "=== Step 3: Train Classifier ==="
python3 train_classifier.py

# 6. Pipeline Complete
echo ""
echo "=========================================="
echo " Pipeline complete: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
