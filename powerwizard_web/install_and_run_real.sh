#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== PowerWizard Real Mode ==="

if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

echo "Using Python: $PYTHON"
exec $PYTHON run_real.py
