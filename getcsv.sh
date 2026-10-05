#!/bin/bash
# Скачивает текущие CSV реестра нумерации в inCSV/archive/YYYYMMDD/ и обновляет inCSV/.
# Дата каталога потом используется импортёром: python -m checkph.importer --backfill
set -euo pipefail

cd "$(dirname "$0")"

DATE_DIR="${1:-$(date +%Y%m%d)}"
ARCHIVE="inCSV/archive/${DATE_DIR}"
BASE_URL="https://opendata.digital.gov.ru/downloads"
FILES=(ABC-3xx.csv ABC-4xx.csv ABC-8xx.csv DEF-9xx.csv)

mkdir -p "${ARCHIVE}" inCSV

for f in "${FILES[@]}"; do
    wget --no-check-certificate -O "${ARCHIVE}/${f}" "${BASE_URL}/${f}"
    cp "${ARCHIVE}/${f}" "inCSV/${f}"
done

echo "Снимок сохранён в ${ARCHIVE}; импорт: .venv/bin/python -m checkph.importer --backfill"
