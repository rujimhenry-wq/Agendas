#!/usr/bin/env bash
# Weekly refresh + digest, suitable for a fresh checkout (scheduled job).
# Usage: scripts/weekly.sh [workers]
set -euo pipefail
cd "$(dirname "$0")/.."
WORKERS="${1:-8}"
python3 -m pip install -q -e . >/dev/null 2>&1 || python3 -m pip install -q -r requirements.txt
njboards init
njboards weekly --workers "$WORKERS" --limit 10 --horizon 35 --pages 25 --skip-fresh 5
# keep the discovered board pages so next week's run starts warm
python3 - <<'PY'
from njboards.db import DB
from njboards.municipalities import export_from_db
from njboards.config import SETTINGS
export_from_db(DB(), SETTINGS.municipalities_file)
PY
echo "digest: data/digests/latest.md"
