# QGIS — PUN-IT shortest path

## Open

```bash
open -a "QGIS-final-3_44_5" qgis/IndoorGML_PUN_IT_pgRouting.qgz
```

Requires DB `indoorgml_punit` with pgrouting-for-indoorgml installed and network refreshed:

```bash
./scripts/install.sh -d indoorgml_punit --refresh --demo
```

## Change start / end

```sql
SELECT * FROM routing.set_path_endpoints('node_new_17', 'node_new_6');
```

Or edit layer **path_params (edit start/end)** in QGIS, then refresh path layers.

Default demo: `node_new_17` → `node_new_6` (length cost ≈ 3268.94).

## Export PNG

```bash
/Applications/QGIS-final-3_44_5.app/Contents/MacOS/python \
  qgis/render_path_map.py
# → pun_it_shortest_path.png
```

## 3D View

View → New 3D Map View. Add `routing.v_qgis_shortest_path_3d` if needed.
PUN-IT sample Z is mostly 0 (flat).
