#!/usr/bin/env bash
# Create IndoorGML database, apply schema SQL, import PUN-IT GML, install routing.
#
# Usage:
#   ./scripts/setup_db.sh -d indoorgml_punit
#   ./scripts/setup_db.sh -d indoorgml_punit --recreate --demo
#   ./scripts/setup_db.sh -d indoorgml_punit -h localhost -U postgres

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB=""
HOST="${PGHOST:-localhost}"
USER="${PGUSER:-postgres}"
PORT="${PGPORT:-5432}"
PASSWORD="${PGPASSWORD:-postgres}"
DO_RECREATE=0
DO_DEMO=0
SKIP_IMPORT=0

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
Create / refresh an IndoorGML + pgRouting database from this repository.

Options:
  -d DB          Database name (required)
  -h HOST        Host (default: localhost or $PGHOST)
  -U USER        User (default: postgres or $PGUSER)
  -p PORT        Port (default: 5432 or $PGPORT)
  --recreate     DROP DATABASE then CREATE (destructive)
  --skip-import  Apply schema only; do not import GML
  --demo         After setup, run routing install + PUN-IT shortest-path demo
  -help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d) DB="$2"; shift 2 ;;
    -h) HOST="$2"; shift 2 ;;
    -U) USER="$2"; shift 2 ;;
    -p) PORT="$2"; shift 2 ;;
    --recreate) DO_RECREATE=1; shift ;;
    --skip-import) SKIP_IMPORT=1; shift ;;
    --demo) DO_DEMO=1; shift ;;
    -help|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$DB" ]]; then
  echo "Error: -d DB is required" >&2
  usage
  exit 1
fi

export PGPASSWORD="$PASSWORD"
PSQL_ADMIN=(psql -h "$HOST" -p "$PORT" -U "$USER" -d postgres -v ON_ERROR_STOP=1)
PSQL=(psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1)

exists="$("${PSQL_ADMIN[@]}" -Atc "SELECT 1 FROM pg_database WHERE datname = '$DB'" || true)"

if [[ "$DO_RECREATE" -eq 1 && "$exists" == "1" ]]; then
  echo ">>> Dropping database $DB"
  "${PSQL_ADMIN[@]}" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB' AND pid <> pg_backend_pid();"
  "${PSQL_ADMIN[@]}" -c "DROP DATABASE \"$DB\";"
  exists=""
fi

if [[ "$exists" != "1" ]]; then
  echo ">>> Creating database $DB"
  "${PSQL_ADMIN[@]}" -c "CREATE DATABASE \"$DB\";"
fi

echo ">>> Enabling PostGIS + pgRouting"
"${PSQL[@]}" -c "CREATE EXTENSION IF NOT EXISTS postgis;"
"${PSQL[@]}" -c "CREATE EXTENSION IF NOT EXISTS pgrouting;"

echo ">>> Applying IndoorGML Core schema"
"${PSQL[@]}" -f "$ROOT/sql/IndoorGML_core.sql"

echo ">>> Applying IndoorGML Navigation schema"
"${PSQL[@]}" -f "$ROOT/sql/IndoorGML_navi.sql"

if [[ "$SKIP_IMPORT" -eq 0 ]]; then
  echo ">>> Importing PUN-IT GML"
  PYTHON=""
  for candidate in \
    "${ROOT}/.venv/bin/python" \
    "${ROOT}/../IndoorGML2_metanorma/src/.venv/bin/python" \
    "$(command -v python3 || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      if "$candidate" -c "import psycopg2, lxml" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
      fi
    fi
  done
  if [[ -z "$PYTHON" ]]; then
    echo "Error: need python3 with psycopg2 and lxml (pip install -r requirements.txt)" >&2
    exit 1
  fi
  echo "    using $PYTHON"
  "$PYTHON" - <<PY
import sys
from pathlib import Path
root = Path(r"$ROOT")
sys.path.insert(0, str(root / "tools"))
from import_gml import import_into_database
ok = import_into_database(
    host="$HOST",
    port=int("$PORT"),
    user="$USER",
    password="$PASSWORD",
    database="$DB",
    gml_path=root / "data" / "sample-PUN-IT-2026-05-06.gml",
    schema_dir=root / "sql",
    apply_schema=False,
)
sys.exit(0 if ok else 1)
PY
fi

echo ">>> Installing pgrouting-for-indoorgml adapter"
INSTALL_ARGS=(-d "$DB" -h "$HOST" -U "$USER" -p "$PORT")
if [[ "$DO_DEMO" -eq 1 ]]; then
  INSTALL_ARGS+=(--demo)
fi
"$ROOT/scripts/install.sh" "${INSTALL_ARGS[@]}"

echo ">>> Setup complete for database: $DB"
