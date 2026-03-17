#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
EXPECTED_PYTHON_VERSION="3.13.3"

PACKAGES=(
  "bidict==0.23.1"
  "blinker==1.9.0"
  "certifi==2026.2.25"
  "charset-normalizer==3.4.6"
  "click==8.3.1"
  "Flask==3.1.3"
  "Flask-SocketIO==5.6.1"
  "h11==0.16.0"
  "idna==3.11"
  "itsdangerous==2.2.0"
  "Jinja2==3.1.6"
  "MarkupSafe==3.0.3"
  "pillow==12.1.1"
  "python-engineio==4.13.1"
  "python-socketio==5.16.1"
  "requests==2.32.5"
  "simple-websocket==1.1.0"
  "urllib3==2.6.3"
  "Werkzeug==3.1.6"
  "wsproto==1.3.2"
)

resolve_python_bin() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
      echo "Python executable not found: ${PYTHON_BIN}" >&2
      exit 1
    fi
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi

  local candidate
  for candidate in python3.13 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      local candidate_version
      candidate_version="$("${candidate}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
      if [[ "${candidate_version}" == "${EXPECTED_PYTHON_VERSION}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    fi
  done

  if command -v pyenv >/dev/null 2>&1; then
    candidate="$(pyenv prefix "${EXPECTED_PYTHON_VERSION}" 2>/dev/null)/bin/python"
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  fi

  return 1
}

if ! PYTHON_BIN="$(resolve_python_bin)"; then
  echo "Could not find Python ${EXPECTED_PYTHON_VERSION}." >&2
  echo "Install it first, or rerun with PYTHON_BIN=/path/to/python${EXPECTED_PYTHON_VERSION%.*} ./start.sh." >&2
  exit 1
fi

PYTHON_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

if [[ "${PYTHON_VERSION}" != "${EXPECTED_PYTHON_VERSION}" ]]; then
  echo "This project was captured with Python ${EXPECTED_PYTHON_VERSION}, but ${PYTHON_BIN} is ${PYTHON_VERSION}." >&2
  echo "Use PYTHON_BIN=/path/to/python${EXPECTED_PYTHON_VERSION%.*} ./start.sh with a matching interpreter." >&2
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade "pip==25.0"
python -m pip install --upgrade --force-reinstall --no-cache-dir "${PACKAGES[@]}"
python -m py_compile "${SCRIPT_DIR}/generate_bingo_card.py" "${SCRIPT_DIR}/realtime_server.py"

cat <<EOF
Environment is ready in ${VENV_DIR}

Activate it with:
source .venv/bin/activate

Generate a card with:
python generate_bingo_card.py --output my-bingo-card.png --web-output generated-bingo-card.html --no-open-browser

Start the realtime server with:
python realtime_server.py --web-card generated-bingo-card.html --host 0.0.0.0 --port 8000
EOF