#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV_DIR="${SCRIPT_DIR}/.venv-pqb-to-supabase"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required but was not found on PATH."
    echo "Install it from https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

uv venv "${VENV_DIR}"
uv pip install --python "${VENV_DIR}" "psycopg[binary]" "python-dotenv"

if [ -f "${VENV_DIR}/bin/activate" ]; then
    ACTIVATE_PATH="${VENV_DIR}/bin/activate"
else
    ACTIVATE_PATH="${VENV_DIR}/Scripts/activate"
fi

cat <<EOF
Installed pqb_to_supabase.py dependencies into:
  ${VENV_DIR}

Use it with:
  . "${ACTIVATE_PATH}"
  python "${SCRIPT_DIR}/pqb_to_supabase.py" --pqb-dir "${SCRIPT_DIR}" --table pleco_merged_entries
EOF
