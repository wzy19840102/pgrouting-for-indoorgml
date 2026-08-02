#!/usr/bin/env bash
# pgrouting-for-indoorgml installer
#
# Usage:
#   ./scripts/install.sh -d indoorgml_punit
#   ./scripts/install.sh -d indoorgml_punit --refresh --demo
#   ./scripts/install.sh -d indoorgml_punit -h localhost -U postgres --layer DS1

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB=""
HOST="${PGHOST:-localhost}"
USER="${PGUSER:-postgres}"
PORT="${PGPORT:-5432}"
LAYER=""
DO_REFRESH=0
DO_DEMO=0
COST_MODE="length"

# Prefer Postgres.app on macOS when psql is not on PATH
if ! command -v psql >/dev/null 2>&1; then
  for candidate in \
    /Applications/Postgres.app/Contents/Versions/latest/bin \
    /Applications/Postgres.app/Contents/Versions/18/bin; do
    if [[ -x "$candidate/psql" ]]; then
      export PATH="$candidate:$PATH"
      break
    fi
  done
fi

usage() {
  cat <<'EOF'
Install pgrouting-for-indoorgml into a PostgreSQL database with IndoorGML Core tables.

Options:
  -d DB          Database name (required)
  -h HOST        Host (default: localhost or $PGHOST)
  -U USER        User (default: postgres or $PGUSER)
  -p PORT        Port (default: 5432 or $PGPORT)
  --layer ID     DualSpaceLayerID for --refresh (default: all layers)
  --cost MODE    length|weight for --refresh (default: length)
  --refresh      Run routing.refresh_network after install
  --demo         Run sql/punit_shortest_path.sql (implies --refresh for DS1)
  -help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d) DB="$2"; shift 2 ;;
    -h) HOST="$2"; shift 2 ;;
    -U) USER="$2"; shift 2 ;;
    -p) PORT="$2"; shift 2 ;;
    --layer) LAYER="$2"; shift 2 ;;
    --cost) COST_MODE="$2"; shift 2 ;;
    --refresh) DO_REFRESH=1; shift ;;
    --demo) DO_DEMO=1; DO_REFRESH=1; shift ;;
    -help|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$DB" ]]; then
  echo "Error: -d DB is required" >&2
  usage
  exit 1
fi

PSQL=(psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1)

echo ">>> Installing pgrouting-for-indoorgml into $DB"
"${PSQL[@]}" -f "$ROOT/sql/install.sql"

if [[ "$DO_DEMO" -eq 1 ]]; then
  echo ">>> Running PUN-IT demo"
  "${PSQL[@]}" -f "$ROOT/sql/punit_shortest_path.sql"
elif [[ "$DO_REFRESH" -eq 1 ]]; then
  echo ">>> Refreshing network (cost=$COST_MODE layer=${LAYER:-ALL})"
  if [[ -n "$LAYER" ]]; then
    "${PSQL[@]}" -c "SELECT * FROM routing.refresh_network('$COST_MODE', '$LAYER');"
  else
    "${PSQL[@]}" -c "SELECT * FROM routing.refresh_network('$COST_MODE', NULL);"
  fi
fi

echo ">>> Done."
echo "    Shortest path: SELECT * FROM routing.shortest_path('<from>', '<to>');"
echo "    QGIS project:  $ROOT/qgis/IndoorGML_PUN_IT_pgRouting.qgz"
