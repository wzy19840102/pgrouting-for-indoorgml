-- pgrouting-for-indoorgml — install core + QGIS layers (no demo side effects)
-- Usage:
--   psql -h localhost -U postgres -d indoorgml_punit -f sql/install.sql
-- Or:
--   ./scripts/install.sh -d indoorgml_punit

\ir core.sql
\ir qgis.sql
