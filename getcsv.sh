#!/bin/bash
# Синхронизация CSV реестра: проверка портала, импорт, архив в zip.
# Дата каталога/архива берётся из индекса downloads/, не из аргумента.
# Опционально: ./getcsv.sh --compress-existing  — только упаковать старые каталоги.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
    echo "Нет .venv/bin/python; создайте окружение: python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
    exit 1
fi

exec .venv/bin/python -m checkph.sync "$@"
