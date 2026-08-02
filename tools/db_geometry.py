"""
Helpers for storing IndoorGML geometries in PostGIS geometry columns.
"""

from __future__ import annotations

import json
from typing import Any

GML_NS = {
    "core": "http://www.opengis.net/indoorgml/2.0/core",
    "gml": "http://www.opengis.net/gml/3.2",
}

GEOM_SQL = "ST_SetSRID(ST_GeomFromGeoJSON(%s), 0)"


def _as_dict(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _close_ring(ring: list) -> list:
    if not ring:
        return ring
    if ring[0] != ring[-1]:
        return ring + [ring[0]]
    return ring


def _polyhedron_to_multipolygon(geom: dict) -> dict:
    coords = geom.get("coordinates") or []
    polygons: list[list[list[list[float]]]] = []
    for face in coords:
        if not face:
            continue
        ring = _close_ring(face[0])
        if len(ring) >= 4:
            polygons.append([ring])
    if not polygons:
        return geom
    return {"type": "MultiPolygon", "coordinates": polygons}


def _is_position(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and isinstance(value[0], (int, float))


def _normalize_geojson(geom: dict) -> dict:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if coords is None:
        return geom

    if gtype == "Polygon" and _is_position(coords[0]):
        return {**geom, "coordinates": [_close_ring(coords)]}
    if gtype == "MultiPolygon" and coords and _is_position(coords[0][0] if coords[0] else None):
        return {**geom, "coordinates": [[_close_ring(ring)] for ring in coords]}
    return geom


def _finalize_geojson(geom: dict | None) -> dict | None:
    if geom is None:
        return None
    if geom.get("type") == "Polyhedron":
        geom = _polyhedron_to_multipolygon(geom)
    return _normalize_geojson(geom)


def _member_geojson(container: dict, *keys: str) -> dict | None:
    for key in keys:
        candidate = _as_dict(container.get(key))
        if candidate and "type" in candidate and "coordinates" in candidate:
            return _finalize_geojson(candidate)
    return None


def indoorjson_to_geojson(value: Any) -> dict | None:
    """Normalize IndoorJSON / nested geometry objects to GeoJSON.

    Prefers geometry3D, then geometry2D, then geometry1D when a wrapper object
    is provided (legacy single-geometry callers).
    """
    geom = _as_dict(value)
    if geom is None:
        return None

    if "type" in geom and "coordinates" in geom:
        return _finalize_geojson(geom)

    return _member_geojson(
        geom,
        "geometry3D",
        "geometry2D",
        "geometry1D",
        "geometry3d",
        "geometry2d",
        "geometry1d",
    )


def split_cellspace_geom(value: Any) -> tuple[str | None, str | None]:
    """Return (geometry2D_json, geometry3D_json) for CellSpace columns."""
    geom = _as_dict(value)
    if geom is None:
        return None, None
    if "type" in geom and "coordinates" in geom:
        # Bare GeoJSON: treat Polygon/MultiPolygon as 2D, Polyhedron as 3D.
        gtype = geom.get("type")
        payload = json.dumps(_finalize_geojson(geom))
        if gtype == "Polyhedron":
            return None, payload
        return payload, None

    g2 = _member_geojson(geom, "geometry2D", "geometry2d")
    g3 = _member_geojson(geom, "geometry3D", "geometry3d")
    return (
        json.dumps(g2) if g2 else None,
        json.dumps(g3) if g3 else None,
    )


def split_cellboundary_geom(value: Any) -> tuple[str | None, str | None]:
    """Return (geometry1D_json, geometry2D_json) for CellBoundary columns.

    IndoorJSON names curve as geometry2D and surface as geometry3D; UML/SQL
    use geometry1D (curve) and geometry2D (surface).
    """
    geom = _as_dict(value)
    if geom is None:
        return None, None
    if "type" in geom and "coordinates" in geom:
        gtype = geom.get("type")
        payload = json.dumps(_finalize_geojson(geom))
        if gtype in {"LineString", "MultiLineString"}:
            return payload, None
        return None, payload

    # UML keys take precedence when geometry1D is present.
    if "geometry1D" in geom or "geometry1d" in geom:
        g1 = _member_geojson(geom, "geometry1D", "geometry1d")
        g2 = _member_geojson(geom, "geometry2D", "geometry2d")
    else:
        # IndoorJSON: geometry2D=curve -> SQL 1D; geometry3D=surface -> SQL 2D
        g1 = _member_geojson(geom, "geometry2D", "geometry2d")
        g2 = _member_geojson(geom, "geometry3D", "geometry3d")

    return (
        json.dumps(g1) if g1 else None,
        json.dumps(g2) if g2 else None,
    )


def geojson_param(value: Any) -> str | None:
    geom = indoorjson_to_geojson(value)
    if geom is None:
        if isinstance(value, dict) and value.get("type") in {"Point", "LineString", "Polygon", "MultiPolygon"}:
            geom = value
        else:
            return None
    return json.dumps(geom)


def external_reference_sql_and_params(value: Any) -> tuple[str, tuple]:
    """Build SQL fragment and params for ExternalReferenceType composite.

    Returns (sql_expr, params). sql_expr is either '%s' with (None,) or a ROW(...)
    constructor with name/uri/informationSystem params.
    """
    ref = _as_dict(value)
    if not ref:
        return "%s", (None,)

    info_sys = ref.get("informationSystem") or ref.get("InformationSystem")
    ext_obj = _as_dict(ref.get("externalObject") or ref.get("ExternalObject")) or {}
    name = ext_obj.get("name") or ext_obj.get("Name")
    uri = ext_obj.get("uri") or ext_obj.get("Uri")
    if name is None and uri is None and info_sys is None:
        return "%s", (None,)

    return (
        'ROW(ROW(%s, %s)::"ExternalObjectReferenceType", %s)::"ExternalReferenceType"',
        (name, uri, info_sys),
    )


def _gml_polygon_to_ring(polygon_elem) -> list[list[float]]:
    pos_nodes = polygon_elem.xpath(".//gml:pos", namespaces=GML_NS)
    ring: list[list[float]] = []
    for pos in pos_nodes:
        if pos.text:
            ring.append([float(c) for c in pos.text.strip().split()])
    return _close_ring(ring)


def gml_elem_to_geojson(elem) -> dict | None:
    """Convert common IndoorGML GML geometry fragments to GeoJSON."""
    if elem is None:
        return None

    polygons = elem.xpath(".//gml:Polygon", namespaces=GML_NS)
    if polygons:
        rings = [_gml_polygon_to_ring(polygon) for polygon in polygons]
        rings = [ring for ring in rings if len(ring) >= 4]
        if len(rings) == 1:
            return {"type": "Polygon", "coordinates": [rings[0]]}
        if len(rings) > 1:
            return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}

    points = elem.xpath(".//gml:Point", namespaces=GML_NS)
    if points:
        pos = points[0].xpath("gml:pos", namespaces=GML_NS)
        if pos and pos[0].text:
            coords = [float(c) for c in pos[0].text.strip().split()]
            return {"type": "Point", "coordinates": coords}

    linestrings = elem.xpath(".//gml:LineString", namespaces=GML_NS)
    if linestrings:
        coords: list[list[float]] = []
        for pos in linestrings[0].xpath("gml:pos", namespaces=GML_NS):
            if pos.text:
                coords.append([float(c) for c in pos.text.strip().split()])
        if coords:
            return {"type": "LineString", "coordinates": coords}

    return None


def gml_elem_to_geojson_param(elem) -> str | None:
    return geojson_param(gml_elem_to_geojson(elem))


def split_gml_cellspace_geom(elem) -> tuple[str | None, str | None]:
    """Return (geometry2D_json, geometry3D_json) from a core:cellSpaceGeom element."""
    if elem is None:
        return None, None
    g2_nodes = elem.xpath(".//core:Geometry2D", namespaces=GML_NS)
    g3_nodes = elem.xpath(".//core:Geometry3D", namespaces=GML_NS)
    g2 = gml_elem_to_geojson_param(g2_nodes[0]) if g2_nodes else None
    g3 = gml_elem_to_geojson_param(g3_nodes[0]) if g3_nodes else None
    if g2 or g3:
        return g2, g3
    # Fallback: undimensioned fragment
    bare = gml_elem_to_geojson_param(elem)
    return bare, None


def split_gml_cellboundary_geom(elem) -> tuple[str | None, str | None]:
    """Return (geometry1D_json, geometry2D_json) from a core:cellBoundaryGeom element."""
    if elem is None:
        return None, None
    g1_nodes = elem.xpath(".//core:Geometry1D", namespaces=GML_NS)
    g2_nodes = elem.xpath(".//core:Geometry2D", namespaces=GML_NS)
    g1 = gml_elem_to_geojson_param(g1_nodes[0]) if g1_nodes else None
    g2 = gml_elem_to_geojson_param(g2_nodes[0]) if g2_nodes else None
    if g1 or g2:
        return g1, g2
    bare = gml_elem_to_geojson(elem)
    if bare is None:
        return None, None
    payload = json.dumps(bare)
    if bare.get("type") in {"LineString", "MultiLineString"}:
        return payload, None
    return None, payload
