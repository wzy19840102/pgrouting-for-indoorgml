-- pgrouting-for-indoorgml — QGIS visualization layers
-- Change start/end: SELECT routing.set_path_endpoints(...); then refresh path layers in QGIS.

CREATE TABLE IF NOT EXISTS routing.path_params (
  id integer PRIMARY KEY CHECK (id = 1),
  source_node_id text NOT NULL,
  target_node_id text NOT NULL,
  directed boolean NOT NULL DEFAULT false,
  updated_at timestamp without time zone DEFAULT clock_timestamp()
);

INSERT INTO routing.path_params (id, source_node_id, target_node_id, directed)
VALUES (1, 'node_new_17', 'node_new_6', false)
ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION routing.set_path_endpoints(
  p_source_node_id text,
  p_target_node_id text,
  p_directed boolean DEFAULT false
)
RETURNS routing.path_params
LANGUAGE plpgsql
AS $$
DECLARE
  r routing.path_params;
BEGIN
  IF routing.node_vid(p_source_node_id) IS NULL THEN
    RAISE EXCEPTION 'Unknown source NodeID: %', p_source_node_id;
  END IF;
  IF routing.node_vid(p_target_node_id) IS NULL THEN
    RAISE EXCEPTION 'Unknown target NodeID: %', p_target_node_id;
  END IF;

  INSERT INTO routing.path_params (id, source_node_id, target_node_id, directed, updated_at)
  VALUES (1, p_source_node_id, p_target_node_id, p_directed, clock_timestamp())
  ON CONFLICT (id) DO UPDATE
  SET
    source_node_id = EXCLUDED.source_node_id,
    target_node_id = EXCLUDED.target_node_id,
    directed       = EXCLUDED.directed,
    updated_at     = clock_timestamp()
  RETURNING * INTO r;

  RETURN r;
END;
$$;

COMMENT ON FUNCTION routing.set_path_endpoints(text, text, boolean) IS
  'Set QGIS path start/end NodeIDs; refresh path layers afterwards';

CREATE OR REPLACE VIEW routing.v_qgis_network_edges AS
SELECT
  eid,
  edge_id,
  source,
  target,
  cost,
  reverse_cost,
  dual_space_layer_id,
  weight,
  length_m,
  ST_Force2D(geom) AS geom
FROM routing.edge_map;

CREATE OR REPLACE VIEW routing.v_qgis_network_nodes AS
SELECT
  vid,
  node_id,
  dual_space_layer_id,
  duality,
  ST_Force2D(geom) AS geom
FROM routing.node_map;

CREATE OR REPLACE VIEW routing.v_qgis_shortest_path AS
SELECT
  1 AS id,
  p.source_node_id,
  p.target_node_id,
  p.directed,
  p.updated_at,
  ST_Force2D(routing.shortest_path_geom(p.source_node_id, p.target_node_id, p.directed)) AS geom,
  (
    SELECT max(sp.agg_cost)
    FROM routing.shortest_path(p.source_node_id, p.target_node_id, p.directed) sp
  ) AS total_cost
FROM routing.path_params p;

CREATE OR REPLACE VIEW routing.v_qgis_path_nodes AS
SELECT
  sp.seq,
  sp.path_seq,
  sp.node_id,
  sp.edge_id,
  sp.cost,
  sp.agg_cost,
  nm.duality,
  ST_Force2D(nm.geom) AS geom
FROM routing.path_params p
CROSS JOIN LATERAL routing.shortest_path(p.source_node_id, p.target_node_id, p.directed) sp
JOIN routing.node_map nm ON nm.node_id = sp.node_id
ORDER BY sp.seq;

CREATE OR REPLACE VIEW routing.v_qgis_path_endpoints AS
SELECT
  'start'::text AS role,
  p.source_node_id AS node_id,
  ST_Force2D(nm.geom) AS geom
FROM routing.path_params p
JOIN routing.node_map nm ON nm.node_id = p.source_node_id
UNION ALL
SELECT
  'end'::text AS role,
  p.target_node_id AS node_id,
  ST_Force2D(nm.geom) AS geom
FROM routing.path_params p
JOIN routing.node_map nm ON nm.node_id = p.target_node_id;

CREATE OR REPLACE VIEW routing.v_qgis_cellspace AS
SELECT
  cs."CellSpaceID" AS cellspace_id,
  cs."CellSpaceName" AS name,
  cs."Level" AS level,
  cs."PrimalSpaceLayerID" AS primal_space_layer_id,
  cs.duality,
  ST_Force2D(
    COALESCE(cs."cellSpaceGeom_geometry3D", cs."cellSpaceGeom_geometry2D")
  ) AS geom
FROM "CellSpace" cs
WHERE cs."cellSpaceGeom_geometry3D" IS NOT NULL
   OR cs."cellSpaceGeom_geometry2D" IS NOT NULL;

CREATE OR REPLACE VIEW routing.v_qgis_shortest_path_3d AS
SELECT
  1 AS id,
  p.source_node_id,
  p.target_node_id,
  p.directed,
  routing.shortest_path_geom(p.source_node_id, p.target_node_id, p.directed) AS geom
FROM routing.path_params p;
