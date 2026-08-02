"""
import_gml.py
------------
Import IndoorGML 2.0 GML (PUN-IT sample) into PostgreSQL.

Usage (from repository root):
    python3 tools/import_gml.py
    python3 tools/import_gml.py --db indoorgml_punit --no-schema

Dependencies:
    pip install psycopg2-binary lxml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg2
from lxml import etree

from db_geometry import GEOM_SQL, geojson_param, gml_elem_to_geojson_param, split_gml_cellboundary_geom, split_gml_cellspace_geom
from db_graph import connects_to_json, rebuild_node_connects_from_edges
from db_names import DB_PUNIT, DEFAULT_PG

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GML = ROOT / "data" / "sample-PUN-IT-2026-05-06.gml"
DEFAULT_SCHEMA_DIR = ROOT / "sql"

# ─────────────────────────── 数据库配置 ───────────────────────────
db_config = {
    **DEFAULT_PG,
    "database": DB_PUNIT,
}

# ─────────────────────────── GML 命名空间 ─────────────────────────
NS = {
    "core": "http://www.opengis.net/indoorgml/2.0/core",
    "gml":  "http://www.opengis.net/gml/3.2",
    "xlink": "http://www.w3.org/1999/xlink",
}

GML_FILE = str(DEFAULT_GML)


# ═══════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════

def strip_hash(ref: str | None) -> str | None:
    """去掉 xlink:href 中的 '#' 前缀。"""
    if ref and ref.startswith("#"):
        return ref[1:]
    return ref


def elem_text(elem, xpath: str) -> str | None:
    """获取 XPath 匹配的第一个元素的文本，不存在时返回 None。"""
    nodes = elem.xpath(xpath, namespaces=NS)
    if nodes:
        text = nodes[0].text
        return text.strip() if text else None
    return None


def elem_attr(elem, xpath: str, attr: str) -> str | None:
    """获取 XPath 匹配的第一个元素的属性值，不存在时返回 None。"""
    nodes = elem.xpath(xpath, namespaces=NS)
    if nodes:
        return nodes[0].get(attr)
    return None


def geometry_to_geojson_param(geom_elem) -> str | None:
    return gml_elem_to_geojson_param(geom_elem)


def point_to_geojson_param(point_elem) -> str | None:
    """Convert <gml:Point> to GeoJSON for ST_GeomFromGeoJSON."""
    if point_elem is None:
        return None
    pos = point_elem.xpath("gml:pos", namespaces=NS)
    if pos and pos[0].text:
        coords = [float(c) for c in pos[0].text.strip().split()]
        return geojson_param({"type": "Point", "coordinates": coords})
    return None


def linestring_to_geojson_param(ls_elem) -> str | None:
    """Convert <gml:LineString> to GeoJSON for ST_GeomFromGeoJSON."""
    if ls_elem is None:
        return None
    positions = ls_elem.xpath("gml:pos", namespaces=NS)
    coords = []
    for p in positions:
        if p.text:
            coords.append([float(c) for c in p.text.strip().split()])
    return geojson_param({"type": "LineString", "coordinates": coords})


# ═══════════════════════════════════════════════════════════════════
#  SQL 辅助
# ═══════════════════════════════════════════════════════════════════

def execute_sql_file(cursor, file_path: str):
    print(f"Executing {file_path}...")
    with open(file_path, "r", encoding="utf-8") as f:
        sql = f.read()
    cursor.execute(sql)


# ═══════════════════════════════════════════════════════════════════
#  GML 解析 & 导入
# ═══════════════════════════════════════════════════════════════════

def import_gml_data(cursor, gml_path: str):
    print(f"\nParsing GML file: {gml_path} ...")
    tree = etree.parse(gml_path)
    root = tree.getroot()

    # ── IndoorFeatures ──────────────────────────────────────────────
    gml_id = root.get("{http://www.opengis.net/gml/3.2}id")
    indoor_features_id = gml_id if gml_id else "IF_gml"

    print(f"  IndoorFeaturesID = {indoor_features_id}")
    cursor.execute(
        'INSERT INTO "IndoorFeatures" ("IndoorFeaturesID", "layers") VALUES (%s, %s)',
        (indoor_features_id, "TL1"),
    )
    # 记录 GML 来源 ID，最终返回给调用者

    # ── ThematicLayer(s) ────────────────────────────────────────────
    thematic_layers = root.xpath("core:layers/core:ThematicLayer", namespaces=NS)
    print(f"  Found {len(thematic_layers)} ThematicLayer(s)")

    for tl in thematic_layers:
        layer_id = tl.get("{http://www.opengis.net/gml/3.2}id")
        theme = elem_text(tl, "core:theme")
        sem_ext_raw = elem_text(tl, "core:semanticExtension")
        sem_ext = (sem_ext_raw.lower() == "true") if sem_ext_raw else None

        cursor.execute(
            'INSERT INTO "ThematicLayer" ("ThematicLayerID", "IndoorFeaturesID", "Theme", "Semanticextension") '
            'VALUES (%s, %s, %s, %s)',
            (layer_id, indoor_features_id, theme, sem_ext),
        )

        # ── PrimalSpaceLayer ────────────────────────────────────────
        primal_elems = tl.xpath("core:primalSpace/core:PrimalSpaceLayer", namespaces=NS)
        for primal in primal_elems:
            primal_id = primal.get("{http://www.opengis.net/gml/3.2}id")
            cursor.execute(
                'INSERT INTO "PrimalSpaceLayer" ("PrimalSpaceLayerID", "ThematicLayerID") VALUES (%s, %s)',
                (primal_id, layer_id),
            )

            # ── CellSpace ──────────────────────────────────────────
            cell_duality_map: dict[str, str] = {}
            cell_members = primal.xpath("core:cellSpaceMember/core:CellSpace", namespaces=NS)
            print(f"    PrimalSpaceLayer {primal_id}: {len(cell_members)} CellSpace(s)")

            for cell in cell_members:
                cell_id = cell.get("{http://www.opengis.net/gml/3.2}id")
                cell_name = elem_text(cell, "core:cellSpaceName")
                level     = elem_text(cell, "core:level")
                poi_raw   = elem_text(cell, "core:poi")
                poi       = (poi_raw.lower() == "true") if poi_raw else None
                duality_href = elem_attr(cell, "core:duality", "{http://www.w3.org/1999/xlink}href")
                duality_ref  = strip_hash(duality_href)

                # 几何体（可能为空）：按 Geometry2D / Geometry3D 写入对应列
                geom_elem = cell.xpath("core:cellSpaceGeom", namespaces=NS)
                g2, g3 = split_gml_cellspace_geom(geom_elem[0]) if geom_elem else (None, None)

                cursor.execute(
                    f'INSERT INTO "CellSpace" '
                    f'("CellSpaceID", "PrimalSpaceLayerID", "CellSpaceName", "Level", "Poi", '
                    f'"cellSpaceGeom_geometry2D", "cellSpaceGeom_geometry3D") '
                    f'VALUES (%s, %s, %s, %s, %s, {GEOM_SQL}, {GEOM_SQL})',
                    (cell_id, primal_id, cell_name, level, poi, g2, g3),
                )
                if duality_ref:
                    cell_duality_map[cell_id] = duality_ref

            # ── CellBoundary ───────────────────────────────────────
            boundary_members = primal.xpath(
                "core:cellBoundaryMember/core:CellBoundary", namespaces=NS
            )
            print(f"    PrimalSpaceLayer {primal_id}: {len(boundary_members)} CellBoundary(s)")
            for boundary in boundary_members:
                bnd_id = boundary.get("{http://www.opengis.net/gml/3.2}id")
                is_virtual_raw = elem_text(boundary, "core:isVirtual")
                is_virtual = (is_virtual_raw.lower() == "true") if is_virtual_raw else None
                geom_elem  = boundary.xpath("core:cellBoundaryGeom", namespaces=NS)
                g1, g2 = split_gml_cellboundary_geom(geom_elem[0]) if geom_elem else (None, None)

                cursor.execute(
                    f'INSERT INTO "CellBoundary" '
                    f'("CellBoundaryID", "PrimalSpaceLayerID", "Isvirtual", '
                    f'"cellBoundaryGeom_geometry1D", "cellBoundaryGeom_geometry2D") '
                    f'VALUES (%s, %s, %s, {GEOM_SQL}, {GEOM_SQL})',
                    (bnd_id, primal_id, is_virtual, g1, g2),
                )

        # ── DualSpaceLayer ──────────────────────────────────────────
        dual_elems = tl.xpath("core:dualSpace/core:DualSpaceLayer", namespaces=NS)
        for dual in dual_elems:
            dual_id = dual.get("{http://www.opengis.net/gml/3.2}id")
            is_logical_raw   = elem_text(dual, "core:isLogical")
            is_directed_raw  = elem_text(dual, "core:isDirected")
            is_logical   = (is_logical_raw.lower()  == "true") if is_logical_raw  else None
            is_directed  = (is_directed_raw.lower() == "true") if is_directed_raw else None

            cursor.execute(
                'INSERT INTO "DualSpaceLayer" '
                '("DualSpaceLayerID", "ThematicLayerID", "Islogical", "Isdirected") '
                'VALUES (%s, %s, %s, %s)',
                (dual_id, layer_id, is_logical, is_directed),
            )

            # ── Node ───────────────────────────────────────────────
            node_members = dual.xpath("core:nodeMember/core:Node", namespaces=NS)
            print(f"    DualSpaceLayer {dual_id}: {len(node_members)} Node(s)")
            for node in node_members:
                node_id = node.get("{http://www.opengis.net/gml/3.2}id")
                duality_href = elem_attr(node, "core:duality", "{http://www.w3.org/1999/xlink}href")
                duality_ref = strip_hash(duality_href)

                point_elem = node.xpath("core:geometry/gml:Point", namespaces=NS)
                geom_param = point_to_geojson_param(point_elem[0]) if point_elem else None

                edge_refs: list[str] = []
                for c in node.xpath("core:connects", namespaces=NS):
                    href = c.get("{http://www.w3.org/1999/xlink}href")
                    edge_ref = strip_hash(href)
                    if edge_ref:
                        edge_refs.append(edge_ref)

                cursor.execute(
                    f'INSERT INTO "Node" ("NodeID", "DualSpaceLayerID", "Geometry", "duality", connects) '
                    f"VALUES (%s, %s, {GEOM_SQL}, %s, %s)",
                    (node_id, dual_id, geom_param, duality_ref, connects_to_json(edge_refs)),
                )

            edge_members = dual.xpath("core:edgeMember/core:Edge", namespaces=NS)
            print(f"    DualSpaceLayer {dual_id}: {len(edge_members)} Edge(s)")

            for edge in edge_members:
                edge_id = edge.get("{http://www.opengis.net/gml/3.2}id")
                weight_raw = elem_text(edge, "core:weight")
                weight = float(weight_raw) if weight_raw else None

                connects = edge.xpath("core:connects", namespaces=NS)
                node_ids = []
                for c in connects[:2]:
                    ref = strip_hash(c.get("{http://www.w3.org/1999/xlink}href"))
                    if ref:
                        node_ids.append(ref)

                ls_elem = edge.xpath("core:geometry/gml:LineString", namespaces=NS)
                geom_param = linestring_to_geojson_param(ls_elem[0]) if ls_elem else None

                cursor.execute(
                    f'INSERT INTO "Edge" '
                    f'("EdgeID", "DualSpaceLayerID", "Weight", "Geometry", connects) '
                    f"VALUES (%s, %s, %s, {GEOM_SQL}, %s)",
                    (edge_id, dual_id, weight, geom_param, connects_to_json(node_ids)),
                )

            rebuilt = rebuild_node_connects_from_edges(cursor)
            print(f"    Rebuilt Node.connects from Edge.connects: {rebuilt} node(s)")

        # ── 更新 CellSpace.duality（在 Node 全部插入后）─────────────
        # cell_duality_map 在每个 primal 中构建，这里统一更新
        # 由于变量作用域：重新扫描所有 primal
        for primal in tl.xpath("core:primalSpace/core:PrimalSpaceLayer", namespaces=NS):
            cdm: dict[str, str] = {}
            for cell in primal.xpath("core:cellSpaceMember/core:CellSpace", namespaces=NS):
                cell_id = cell.get("{http://www.opengis.net/gml/3.2}id")
                duality_href = elem_attr(cell, "core:duality", "{http://www.w3.org/1999/xlink}href")
                duality_ref  = strip_hash(duality_href)
                if duality_ref:
                    cdm[cell_id] = duality_ref
            for cell_id, node_id in cdm.items():
                cursor.execute(
                    'UPDATE "CellSpace" SET "duality" = %s WHERE "CellSpaceID" = %s',
                    (node_id, cell_id),
                )
            print(f"    Updated duality for {len(cdm)} CellSpace record(s) in primal {primal.get('{http://www.opengis.net/gml/3.2}id')}.")

    return indoor_features_id


# ═══════════════════════════════════════════════════════════════════
#  类别对比分析
# ═══════════════════════════════════════════════════════════════════

def compare_classes(cursor, gml_if_id: str):
    """
    对比 GML 导入数据（IndoorFeaturesID = gml_if_id）与
    其他来源数据（IndoorFeaturesID != gml_if_id）在各表中的类别分布。
    """
    print("\n" + "=" * 60)
    print(f"  类别对比分析  GML来源={gml_if_id!r}")
    print("=" * 60)

    # ── 1. CellSpace 的 Level 分布 ──────────────────────────────────
    print("\n[1] CellSpace Level 分布")
    print(f"  {'Level':<10} {'GML 数量':>10} {'JSON 数量':>10} {'差异':>10}")
    print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    cursor.execute("""
        SELECT
            cs."Level",
            SUM(CASE WHEN psl."ThematicLayerID" IN (
                    SELECT "ThematicLayerID" FROM "ThematicLayer"
                    WHERE "IndoorFeaturesID" = %s
                ) THEN 1 ELSE 0 END) AS gml_count,
            SUM(CASE WHEN psl."ThematicLayerID" IN (
                    SELECT "ThematicLayerID" FROM "ThematicLayer"
                    WHERE "IndoorFeaturesID" != %s
                ) THEN 1 ELSE 0 END) AS other_count
        FROM "CellSpace" cs
        JOIN "PrimalSpaceLayer" psl ON cs."PrimalSpaceLayerID" = psl."PrimalSpaceLayerID"
        GROUP BY cs."Level"
        ORDER BY cs."Level"
    """, (gml_if_id, gml_if_id))
    rows = cursor.fetchall()
    for row in rows:
        level, gml_c, other_c = row
        diff = gml_c - other_c
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        print(f"  {str(level):<10} {gml_c:>10} {other_c:>10} {diff_str:>10}")
    if not rows:
        print("  (无数据)")

    # ── 2. CellSpace 命名前缀（类型推断）────────────────────────────
    print("\n[2] CellSpace 命名前缀（类型）分布")
    print(f"  {'前缀':<20} {'GML 数量':>10} {'其他来源':>10} {'差异':>10}")
    print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*10}")
    cursor.execute("""
        WITH prefixed AS (
            SELECT
                CASE
                    WHEN "CellSpaceName" ILIKE 'Virtual%%' THEN 'Virtual'
                    WHEN "CellSpaceName" ILIKE 'Door%%'    THEN 'Door'
                    WHEN "CellSpaceName" ILIKE 'Corridor%%' THEN 'Corridor'
                    WHEN "CellSpaceName" ILIKE 'Stair%%'   THEN 'Stair'
                    WHEN "CellSpaceName" ILIKE 'Elevator%%' THEN 'Elevator'
                    WHEN "CellSpaceName" ILIKE 'Lab%%'     THEN 'Lab'
                    WHEN "CellSpaceName" ILIKE 'Office%%'  THEN 'Office'
                    WHEN "CellSpaceName" ILIKE 'Faculty%%' THEN 'Faculty Office'
                    WHEN "CellSpaceName" ILIKE 'Lounge%%'  THEN 'Lounge'
                    ELSE 'Other'
                END AS prefix,
                psl."ThematicLayerID"
            FROM "CellSpace" cs
            JOIN "PrimalSpaceLayer" psl ON cs."PrimalSpaceLayerID" = psl."PrimalSpaceLayerID"
        )
        SELECT
            prefix,
            SUM(CASE WHEN "ThematicLayerID" IN (
                    SELECT "ThematicLayerID" FROM "ThematicLayer"
                    WHERE "IndoorFeaturesID" = %s
                ) THEN 1 ELSE 0 END) AS gml_count,
            SUM(CASE WHEN "ThematicLayerID" IN (
                    SELECT "ThematicLayerID" FROM "ThematicLayer"
                    WHERE "IndoorFeaturesID" != %s
                ) THEN 1 ELSE 0 END) AS other_count
        FROM prefixed
        GROUP BY prefix
        ORDER BY prefix
    """, (gml_if_id, gml_if_id))
    rows = cursor.fetchall()
    for row in rows:
        prefix, gml_c, other_c = row
        diff = gml_c - other_c
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        print(f"  {str(prefix):<20} {gml_c:>10} {other_c:>10} {diff_str:>10}")
    if not rows:
        print("  (无数据)")

    # ── 3. 各主要表的记录总数对比 ──────────────────────────────────
    print("\n[3] 各主要表记录数对比")
    print(f"  {'表名':<30} {'GML 数量':>10} {'JSON 数量':>10}")
    print(f"  {'-'*30} {'-'*10} {'-'*10}")

    tables = [
        ("CellSpace",       '"PrimalSpaceLayerID"', "PrimalSpaceLayer", '"ThematicLayerID"'),
        ("CellBoundary",    '"PrimalSpaceLayerID"', "PrimalSpaceLayer", '"ThematicLayerID"'),
        ("Node",            '"DualSpaceLayerID"',   "DualSpaceLayer",   '"ThematicLayerID"'),
        ("Edge",            '"DualSpaceLayerID"',   "DualSpaceLayer",   '"ThematicLayerID"'),
    ]

    for (t_name, t_fk, mid_table, mid_fk) in tables:
        cursor.execute(f"""
            SELECT
                SUM(CASE WHEN m.{mid_fk} IN (
                        SELECT "ThematicLayerID" FROM "ThematicLayer"
                        WHERE "IndoorFeaturesID" = %s
                    ) THEN 1 ELSE 0 END) AS gml_count,
                SUM(CASE WHEN m.{mid_fk} IN (
                        SELECT "ThematicLayerID" FROM "ThematicLayer"
                        WHERE "IndoorFeaturesID" != %s
                    ) THEN 1 ELSE 0 END) AS other_count
            FROM "{t_name}" t
            JOIN "{mid_table}" m ON t.{t_fk} = m."{mid_table}ID"
        """, (gml_if_id, gml_if_id))
        row = cursor.fetchone()
        gml_c   = row[0] or 0
        other_c = row[1] or 0
        print(f"  {t_name:<30} {gml_c:>10} {other_c:>10}")

    # ── 4. ThematicLayer 数量对比 ───────────────────────────────────
    print("\n[4] ThematicLayer 数量")
    cursor.execute("""
        SELECT
            SUM(CASE WHEN "IndoorFeaturesID" = %s THEN 1 ELSE 0 END) AS gml_count,
            SUM(CASE WHEN "IndoorFeaturesID" != %s THEN 1 ELSE 0 END) AS other_count
        FROM "ThematicLayer"
    """, (gml_if_id, gml_if_id))
    row = cursor.fetchone()
    print(f"  GML 来源 ({gml_if_id}): {row[0] or 0}  |  其他来源: {row[1] or 0}")

    # ── 5. 总结 ──────────────────────────────────────────────────────
    print("\n[5] 差异总结")
    cursor.execute(
        'SELECT COUNT(*) FROM "IndoorFeatures" WHERE "IndoorFeaturesID" = %s',
        (gml_if_id,)
    )
    gml_exists = cursor.fetchone()[0] > 0
    cursor.execute(
        'SELECT COUNT(*) FROM "IndoorFeatures" WHERE "IndoorFeaturesID" != %s',
        (gml_if_id,)
    )
    other_exists = cursor.fetchone()[0] > 0

    if gml_exists and other_exists:
        print(f"  ✅ 数据库中同时存在 GML 数据 ({gml_if_id}) 与其他来源数据。")
    elif gml_exists:
        print(f"  ℹ️  数据库中只有 GML 数据 ({gml_if_id})，无其他来源数据（对比列均为 0 属正常）。")
    else:
        print("  ❌ 未检测到 GML 来源数据（导入可能失败）。")

    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════
#  主函数
# ═══════════════════════════════════════════════════════════════════

def import_into_database(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    gml_path: str | Path,
    schema_dir: str | Path = DEFAULT_SCHEMA_DIR,
    apply_schema: bool = True,
    compare: bool = True,
) -> bool:
    """Apply IndoorGML schema (optional), import GML, optionally compare classes."""
    gml_path = Path(gml_path)
    schema_dir = Path(schema_dir)
    if not gml_path.is_file():
        print(f"❌ GML file not found: {gml_path}")
        return False

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
        )
        conn.autocommit = False
        cursor = conn.cursor()

        if apply_schema:
            execute_sql_file(cursor, str(schema_dir / "IndoorGML_core.sql"))
            execute_sql_file(cursor, str(schema_dir / "IndoorGML_navi.sql"))

        gml_if_id = import_gml_data(cursor, str(gml_path))
        conn.commit()
        print("\n✅ GML 数据导入完成。")

        if compare:
            compare_classes(cursor, gml_if_id)
        return True
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import PUN-IT IndoorGML into PostgreSQL")
    parser.add_argument("--db", default=DB_PUNIT, help="Database name")
    parser.add_argument("--host", default=DEFAULT_PG["host"])
    parser.add_argument("--port", type=int, default=DEFAULT_PG["port"])
    parser.add_argument("--user", default=DEFAULT_PG["user"])
    parser.add_argument("--password", default=DEFAULT_PG["password"])
    parser.add_argument("--gml", default=str(DEFAULT_GML), help="Path to .gml file")
    parser.add_argument(
        "--schema-dir",
        default=str(DEFAULT_SCHEMA_DIR),
        help="Directory containing IndoorGML_core.sql / IndoorGML_navi.sql",
    )
    parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Skip applying IndoorGML SQL (schema already installed)",
    )
    parser.add_argument("--no-compare", action="store_true", help="Skip class comparison report")
    args = parser.parse_args(argv)

    ok = import_into_database(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.db,
        gml_path=args.gml,
        schema_dir=args.schema_dir,
        apply_schema=not args.no_schema,
        compare=not args.no_compare,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
