-- pgrouting-for-indoorgml example: sample-PUN-IT-2026-05-06.gml → database indoorgml_punit
-- Dual space layer DS1 — 218 nodes, 229 edges.
-- Demo path: node_new_17 → node_new_6
--
-- Prerequisite: sql/install.sql already applied.

\echo === Refresh routing network from IndoorGML (length cost) ===
SELECT * FROM routing.refresh_network('length', 'DS1');

\echo === Network summary ===
SELECT
  (SELECT count(*) FROM routing.node_map) AS nodes,
  (SELECT count(*) FROM routing.edge_map) AS edges,
  round(avg(cost)::numeric, 2) AS avg_edge_cost
FROM routing.edge_map;

\echo === Shortest path node_new_17 → node_new_6 ===
SELECT
  seq,
  node_id,
  edge_id,
  round(cost::numeric, 2) AS cost,
  round(agg_cost::numeric, 2) AS agg_cost
FROM routing.shortest_path('node_new_17', 'node_new_6');

\echo === Save as IndoorGML Route ===
SELECT routing.save_route('route_punit_demo_01', 'node_new_17', 'node_new_6');

SELECT
  "RouteID",
  "Creationdate",
  "routeNode",
  "routeEdge"
FROM "Route"
WHERE "RouteID" = 'route_punit_demo_01';

\echo === Set QGIS path endpoints ===
SELECT * FROM routing.set_path_endpoints('node_new_17', 'node_new_6');

\echo === Optional: hop-count routing (IndoorGML Weight) ===
SELECT * FROM routing.refresh_network('weight', 'DS1');
SELECT
  seq,
  node_id,
  edge_id,
  cost,
  agg_cost
FROM routing.shortest_path('node_new_17', 'node_new_6');

-- Restore length-based network for interactive / QGIS use
SELECT * FROM routing.refresh_network('length', 'DS1');
SELECT * FROM routing.set_path_endpoints('node_new_17', 'node_new_6');
