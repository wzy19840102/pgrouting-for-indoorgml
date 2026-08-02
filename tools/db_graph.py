"""
db_graph.py
-----------
IndoorGML 图连接属性 helpers（connects / routeNode / routeEdge 以 jsonb 数组存储）。
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

from psycopg2.extras import Json


def connects_value(ids: Iterable[str] | None):
    """适配 psycopg2 的 jsonb 写入值。"""
    items = [str(i) for i in (ids or []) if i]
    return Json(items) if items else None


# 兼容旧调用名
connects_to_json = connects_value


def connects_from_json(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if x]
    raise TypeError(f"Unsupported connects value: {type(raw)!r}")


def edge_endpoints(connects_raw) -> tuple[str | None, str | None]:
    nodes = connects_from_json(connects_raw)
    if len(nodes) >= 2:
        return nodes[0], nodes[1]
    if len(nodes) == 1:
        return nodes[0], None
    return None, None


def rebuild_node_connects_from_edges(cursor) -> int:
    """从 Edge.connects 反推并写回 Node.connects（GML 导入后使用）。"""
    cursor.execute('SELECT "EdgeID", connects FROM "Edge"')
    node_to_edges: dict[str, list[str]] = defaultdict(list)
    for edge_id, connects_raw in cursor.fetchall():
        if not edge_id:
            continue
        n1, n2 = edge_endpoints(connects_raw)
        if n1:
            node_to_edges[n1].append(edge_id)
        if n2:
            node_to_edges[n2].append(edge_id)

    cursor.execute('SELECT "NodeID" FROM "Node"')
    updated = 0
    for (node_id,) in cursor.fetchall():
        edge_ids = list(dict.fromkeys(node_to_edges.get(node_id, [])))
        cursor.execute(
            'UPDATE "Node" SET connects = %s WHERE "NodeID" = %s',
            (connects_value(edge_ids), node_id),
        )
        updated += 1
    return updated


def parse_graph_from_db(cursor) -> tuple[dict[str, list[tuple[str, str]]], list[tuple[str, str, str]]]:
    """从 Edge.connects 构建无向邻接表。"""
    cursor.execute('SELECT "EdgeID", connects FROM "Edge"')
    edges: list[tuple[str, str, str]] = []
    adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge_id, connects_raw in cursor.fetchall():
        n1, n2 = edge_endpoints(connects_raw)
        if not edge_id or not n1 or not n2:
            continue
        edges.append((edge_id, n1, n2))
        adj[n1].append((n2, edge_id))
        adj[n2].append((n1, edge_id))
    return adj, edges


ROUTE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS "Route"
(
    "Creationdate" timestamp without time zone NULL,
    "RouteID" varchar(100) NOT NULL,
    "routeNode" jsonb NULL,
    "routeEdge" jsonb NULL
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'PK_Route') THEN
        ALTER TABLE "Route" ADD CONSTRAINT "PK_Route" PRIMARY KEY ("RouteID");
    END IF;
END $$;
"""
