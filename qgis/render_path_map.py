#!/usr/bin/env python3
"""Render IndoorGML PUN-IT pgRouting path to PNG using QGIS (PyQGIS).

Usage:
  /Applications/QGIS-final-3_44_5.app/Contents/MacOS/python \\
    pgrouting4indoorgml/qgis/render_path_map.py
"""

from __future__ import annotations

import os
import sys

QGIS_ROOT = "/Applications/QGIS-final-3_44_5.app/Contents"
QGIS_PREFIX = os.environ.get("QGIS_PREFIX_PATH", f"{QGIS_ROOT}/MacOS")
os.environ["QGIS_PREFIX_PATH"] = QGIS_PREFIX
os.environ["PROJ_LIB"] = f"{QGIS_ROOT}/Resources/qgis/proj"
os.environ["GDAL_DATA"] = f"{QGIS_ROOT}/Resources/gdal"
os.environ.setdefault(
    "QGIS_PLUGINPATH",
    f"{QGIS_ROOT}/PlugIns/qgis",
)

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProject,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
    QgsMapSettings,
    QgsMapRendererParallelJob,
    QgsProviderRegistry,
)
from qgis.PyQt.QtCore import QSize, QTimer  # noqa: E402
from qgis.PyQt.QtGui import QColor, QImage, QPainter  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "images")
OUT_PNG = os.path.join(OUT_DIR, "punit_shortest_path.png")

URI_BASE = (
    "dbname='indoorgml_punit' host=localhost port=5432 user='postgres' "
    "sslmode=disable estimatedmetadata=true checkPrimaryKeyUnicity='1' srid=0"
)


def pg_layer(name: str, table: str, geom: str, gtype: str, key: str) -> QgsVectorLayer:
    uri = (
        f"{URI_BASE} key='{key}' type={gtype} "
        f'table="routing"."{table}" ({geom}) sql='
    )
    layer = QgsVectorLayer(uri, name, "postgres")
    if not layer.isValid():
        # Fallback: OGR PostgreSQL driver
        ogr_uri = (
            f"PG:dbname=indoorgml_punit host=localhost port=5432 user=postgres "
            f"|layername=routing.{table}"
        )
        layer = QgsVectorLayer(ogr_uri, name, "ogr")
    if not layer.isValid():
        raise RuntimeError(
            f"Invalid layer {name}: {uri}\nerror={layer.error().message()}"
        )
    return layer


def main() -> int:
    QgsApplication.setPrefixPath(QGIS_PREFIX, True)
    app = QgsApplication([], False)
    app.setPluginPath(f"{QGIS_ROOT}/PlugIns/qgis")
    app.initQgis()

    providers = QgsProviderRegistry.instance().providerList()
    if "postgres" not in providers:
        print("WARNING: postgres provider missing; using ogr fallback", file=sys.stderr)
        print("providers:", providers, file=sys.stderr)

    cells = pg_layer("CellSpace", "v_qgis_cellspace", "geom", "MultiPolygon", "cellspace_id")
    edges = pg_layer("Network edges", "v_qgis_network_edges", "geom", "LineString", "eid")
    path = pg_layer("Shortest path", "v_qgis_shortest_path", "geom", "LineString", "id")
    nodes = pg_layer("Path nodes", "v_qgis_path_nodes", "geom", "Point", "seq")
    ends = pg_layer("Start/End", "v_qgis_path_endpoints", "geom", "Point", "role")

    cells.setRenderer(
        QgsSingleSymbolRenderer(
            QgsFillSymbol.createSimple(
                {
                    "color": "70,130,180,40",
                    "outline_color": "70,130,180,140",
                    "outline_width": "0.2",
                }
            )
        )
    )
    edges.setRenderer(
        QgsSingleSymbolRenderer(
            QgsLineSymbol.createSimple(
                {"line_color": "140,140,140,160", "line_width": "0.4"}
            )
        )
    )
    path.setRenderer(
        QgsSingleSymbolRenderer(
            QgsLineSymbol.createSimple(
                {"line_color": "220,20,60,255", "line_width": "1.6"}
            )
        )
    )
    nodes.setRenderer(
        QgsSingleSymbolRenderer(
            QgsMarkerSymbol.createSimple(
                {
                    "name": "circle",
                    "color": "255,165,0,255",
                    "size": "2.2",
                    "outline_color": "30,30,30,255",
                }
            )
        )
    )

    start_sym = QgsMarkerSymbol.createSimple(
        {"name": "star", "color": "0,160,0,255", "size": "5"}
    )
    end_sym = QgsMarkerSymbol.createSimple(
        {"name": "triangle", "color": "200,0,0,255", "size": "5"}
    )
    ends.setRenderer(
        QgsCategorizedSymbolRenderer(
            "role",
            [
                QgsRendererCategory("start", start_sym, "Start"),
                QgsRendererCategory("end", end_sym, "End"),
            ],
        )
    )

    project = QgsProject.instance()
    for lyr in (cells, edges, path, nodes, ends):
        project.addMapLayer(lyr)

    extent = edges.extent()
    if extent.isNull() or extent.isEmpty():
        extent = cells.extent()
    extent.grow(extent.width() * 0.05)

    settings = QgsMapSettings()
    settings.setLayers([ends, nodes, path, edges, cells])
    settings.setBackgroundColor(QColor(250, 250, 248))
    settings.setOutputSize(QSize(1600, 900))
    settings.setExtent(extent)

    image = QImage(settings.outputSize(), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(250, 250, 248).rgb())
    painter = QPainter(image)
    job = QgsMapRendererParallelJob(settings)

    done = {"ok": False}

    def finished():
        painter.drawImage(0, 0, job.renderedImage())
        painter.end()
        os.makedirs(OUT_DIR, exist_ok=True)
        image.save(OUT_PNG, "PNG")
        done["ok"] = True
        app.quit()

    job.finished.connect(finished)
    job.start()

    QTimer.singleShot(30000, app.quit)
    app.exec_()
    app.exitQgis()

    if not done["ok"] or not os.path.isfile(OUT_PNG):
        print("Render failed", file=sys.stderr)
        return 1
    print(OUT_PNG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
