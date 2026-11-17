#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
trap 'code=$?; if [ "$code" -ne 0 ]; then printf "\nNo se pudo iniciar Diamond IQ. Revisa el mensaje anterior.\n"; read -r -p "Presiona Enter para cerrar… " _; fi' EXIT
python_bin=""
for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3.13 python3.12 python3.11 python3.10; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    python_bin="$candidate"
    break
  fi
done
if [ -z "$python_bin" ]; then
  printf 'Instala Python 3.10 o posterior desde https://www.python.org/downloads/macos/ y vuelve a abrir este archivo.\n'
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  printf 'Preparando el entorno local…\n'
  "$python_bin" -m venv .venv
fi
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
requirements_hash="$(shasum -a 256 requirements.txt | cut -d ' ' -f 1)"
installed_hash="$(cat .venv/.diamond-requirements 2>/dev/null || true)"
if [ "$requirements_hash" != "$installed_hash" ]; then
  printf 'Instalando o actualizando dependencias (requiere Internet)…\n'
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  printf '%s' "$requirements_hash" > .venv/.diamond-requirements
fi
.venv/bin/python scripts/launch_local.py
