-- pgrouting-for-indoorgml — core adapter
-- IndoorGML "Node"/"Edge" remain the source of truth.
-- This schema maps string IDs to integer ids required by pgRouting.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

DROP SCHEMA IF EXISTS routing CASCADE;
CREATE SCHEMA routing;

COMMENT ON SCHEMA routing IS
  'pgrouting-for-indoorgml: pgRouting adapter over IndoorGML Core dual-space Node/Edge';

-- Integer vertex map (IndoorGML NodeID is varchar)
CREATE TABLE routing.node_map (
  vid                 bigserial PRIMARY KEY,
  node_id             text UNIQUE NOT NULL,
  dual_space_layer_id text,
  duality             text,
  geom                geometry
);

COMMENT ON TABLE routing.node_map IS
  'Maps IndoorGML Node.NodeID to integer vid for pgRouting';

-- Integer edge map with source/target/cost for pgr_* functions
CREATE TABLE routing.edge_map (
  eid                 bigserial PRIMARY KEY,
  edge_id             text UNIQUE NOT NULL,
  source              bigint NOT NULL REFERENCES routing.node_map (vid),
  target              bigint NOT NULL REFERENCES routing.node_map (vid),
  cost                double precision NOT NULL,
  reverse_cost        double precision NOT NULL,
  dual_space_layer_id text,
  weight              real,
  length_m            double precision,
  geom                geometry
);

CREATE INDEX edge_map_source_idx ON routing.edge_map (source);
CREATE INDEX edge_map_target_idx ON routing.edge_map (target);
CREATE INDEX edge_map_layer_idx  ON routing.edge_map (dual_space_layer_id);

COMMENT ON TABLE routing.edge_map IS
  'Maps IndoorGML Edge to pgRouting edges_sql (id, source, target, cost, reverse_cost)';

CREATE OR REPLACE VIEW routing.v_edges_sql AS
SELECT
  eid AS id,
  source,
  target,
  cost,
  reverse_cost
FROM routing.edge_map;

COMMENT ON VIEW routing.v_edges_sql IS
  'edges_sql projection for pgRouting path algorithms';

CREATE OR REPLACE FUNCTION routing.node_vid(p_node_id text)
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
  SELECT vid FROM routing.node_map WHERE node_id = p_node_id;
$$;

-- Rebuild maps from IndoorGML Core
-- cost_mode: 'length' (default) | 'weight'
-- p_dual_space_layer_id: NULL = all layers, e.g. 'DS1'
CREATE OR REPLACE FUNCTION routing.refresh_network(
  p_cost_mode text DEFAULT 'length',
  p_dual_space_layer_id text DEFAULT NULL
)
RETURNS TABLE (
  node_count bigint,
  edge_count bigint,
  cost_mode text,
  dual_space_layer_id text
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_mode text := lower(coalesce(p_cost_mode, 'length'));
BEGIN
  IF v_mode NOT IN ('length', 'weight') THEN
    RAISE EXCEPTION 'cost_mode must be ''length'' or ''weight'', got: %', p_cost_mode;
  END IF;

  TRUNCATE routing.edge_map, routing.node_map RESTART IDENTITY;

  INSERT INTO routing.node_map (node_id, dual_space_layer_id, duality, geom)
  SELECT
    n."NodeID",
    n."DualSpaceLayerID",
    n."duality",
    n."Geometry"
  FROM "Node" n
  WHERE p_dual_space_layer_id IS NULL
     OR n."DualSpaceLayerID" = p_dual_space_layer_id
  ORDER BY n."NodeID";

  INSERT INTO routing.edge_map (
    edge_id, source, target, cost, reverse_cost,
    dual_space_layer_id, weight, length_m, geom
  )
  SELECT
    e."EdgeID",
    s.vid,
    t.vid,
    CASE
      WHEN v_mode = 'weight' THEN
        COALESCE(
          NULLIF(e."Weight"::double precision, 0),
          NULLIF(ST_3DLength(e."Geometry"), 0),
          NULLIF(ST_Length(e."Geometry"), 0),
          1.0
        )
      ELSE
        COALESCE(
          NULLIF(ST_3DLength(e."Geometry"), 0),
          NULLIF(ST_Length(e."Geometry"), 0),
          NULLIF(e."Weight"::double precision, 0),
          1.0
        )
    END AS cost,
    CASE
      WHEN v_mode = 'weight' THEN
        COALESCE(
          NULLIF(e."Weight"::double precision, 0),
          NULLIF(ST_3DLength(e."Geometry"), 0),
          NULLIF(ST_Length(e."Geometry"), 0),
          1.0
        )
      ELSE
        COALESCE(
          NULLIF(ST_3DLength(e."Geometry"), 0),
          NULLIF(ST_Length(e."Geometry"), 0),
          NULLIF(e."Weight"::double precision, 0),
          1.0
        )
    END AS reverse_cost,
    e."DualSpaceLayerID",
    e."Weight",
    COALESCE(ST_3DLength(e."Geometry"), ST_Length(e."Geometry")),
    e."Geometry"
  FROM "Edge" e
  JOIN routing.node_map s ON s.node_id = e.connects->>0
  JOIN routing.node_map t ON t.node_id = e.connects->>1
  WHERE e.connects IS NOT NULL
    AND jsonb_typeof(e.connects) = 'array'
    AND jsonb_array_length(e.connects) = 2
    AND (p_dual_space_layer_id IS NULL
         OR e."DualSpaceLayerID" = p_dual_space_layer_id);

  RETURN QUERY
  SELECT
    (SELECT count(*) FROM routing.node_map),
    (SELECT count(*) FROM routing.edge_map),
    v_mode,
    p_dual_space_layer_id;
END;
$$;

COMMENT ON FUNCTION routing.refresh_network(text, text) IS
  'Rebuild routing maps from IndoorGML Node/Edge using connects + length or Weight';

-- Shortest path between two IndoorGML NodeIDs (pgr_dijkstra)
CREATE OR REPLACE FUNCTION routing.shortest_path(
  p_source_node_id text,
  p_target_node_id text,
  p_directed boolean DEFAULT false
)
RETURNS TABLE (
  seq integer,
  path_seq integer,
  node_vid bigint,
  node_id text,
  edge_eid bigint,
  edge_id text,
  cost double precision,
  agg_cost double precision,
  geom geometry
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_source bigint;
  v_target bigint;
BEGIN
  v_source := routing.node_vid(p_source_node_id);
  v_target := routing.node_vid(p_target_node_id);

  IF v_source IS NULL THEN
    RAISE EXCEPTION 'Unknown source NodeID: %', p_source_node_id;
  END IF;
  IF v_target IS NULL THEN
    RAISE EXCEPTION 'Unknown target NodeID: %', p_target_node_id;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM routing.edge_map) THEN
    RAISE EXCEPTION 'routing.edge_map is empty — run SELECT routing.refresh_network() first';
  END IF;

  RETURN QUERY
  SELECT
    r.seq::integer,
    r.path_seq::integer,
    r.node::bigint AS node_vid,
    nm.node_id,
    NULLIF(r.edge, -1)::bigint AS edge_eid,
    em.edge_id,
    r.cost::double precision,
    r.agg_cost::double precision,
    COALESCE(em.geom, nm.geom) AS geom
  FROM pgr_dijkstra(
    'SELECT eid AS id, source, target, cost, reverse_cost FROM routing.edge_map',
    v_source,
    v_target,
    p_directed
  ) AS r
  LEFT JOIN routing.node_map nm ON nm.vid = r.node
  LEFT JOIN routing.edge_map em ON em.eid = r.edge
  ORDER BY r.seq;
END;
$$;

COMMENT ON FUNCTION routing.shortest_path(text, text, boolean) IS
  'Shortest path between IndoorGML NodeIDs via pgr_dijkstra';

CREATE OR REPLACE FUNCTION routing.shortest_path_geom(
  p_source_node_id text,
  p_target_node_id text,
  p_directed boolean DEFAULT false
)
RETURNS geometry
LANGUAGE sql
STABLE
AS $$
  SELECT ST_LineMerge(ST_Collect(geom ORDER BY seq))
  FROM routing.shortest_path(p_source_node_id, p_target_node_id, p_directed)
  WHERE edge_id IS NOT NULL AND geom IS NOT NULL;
$$;

CREATE OR REPLACE FUNCTION routing.save_route(
  p_route_id text,
  p_source_node_id text,
  p_target_node_id text,
  p_directed boolean DEFAULT false
)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
  v_nodes jsonb;
  v_edges jsonb;
  v_n bigint;
BEGIN
  SELECT
    coalesce(jsonb_agg(sp.node_id ORDER BY sp.seq), '[]'::jsonb),
    coalesce(
      jsonb_agg(sp.edge_id ORDER BY sp.seq) FILTER (WHERE sp.edge_id IS NOT NULL),
      '[]'::jsonb
    ),
    count(*)
  INTO v_nodes, v_edges, v_n
  FROM routing.shortest_path(p_source_node_id, p_target_node_id, p_directed) sp;

  IF v_n = 0 THEN
    RAISE EXCEPTION 'No path found from % to %', p_source_node_id, p_target_node_id;
  END IF;

  INSERT INTO "Route" ("RouteID", "Creationdate", "routeNode", "routeEdge")
  VALUES (p_route_id, clock_timestamp(), v_nodes, v_edges)
  ON CONFLICT ("RouteID") DO UPDATE
  SET
    "Creationdate" = EXCLUDED."Creationdate",
    "routeNode"    = EXCLUDED."routeNode",
    "routeEdge"    = EXCLUDED."routeEdge";

  RETURN p_route_id;
END;
$$;

COMMENT ON FUNCTION routing.save_route(text, text, text, boolean) IS
  'Compute shortest path and upsert into IndoorGML Route.routeNode / routeEdge';
