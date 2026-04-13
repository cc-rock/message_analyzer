#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

if ! command -v python3.9 >/dev/null 2>&1; then
    echo "python3.9 was not found in PATH." >&2
    echo "Install Python 3.9.x first, then re-run this script." >&2
    exit 1
fi

PYTHON_VERSION="$(python3.9 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"

case "${PYTHON_VERSION}" in
    3.9.*) ;;
    *)
        echo "Expected Python 3.9.x, found ${PYTHON_VERSION}." >&2
        exit 1
        ;;
esac

echo "Creating virtual environment in ${VENV_DIR}"
python3.9 -m venv "${VENV_DIR}"

echo "Upgrading pip"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip

echo "Installing dependencies"
"${VENV_DIR}/bin/pip" install "holidays==0.83" "openpyxl"

cat <<EOF

Setup complete.

To activate the virtual environment:
  source "${VENV_DIR}/bin/activate"
EOF
