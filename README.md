# pgrouting-for-indoorgml

从 IndoorGML 对偶空间网络提取图，用 [pgRouting](https://pgrouting.org/) 计算最短路径，不修改 IndoorGML Core 表结构。

## Layout

```text
pgrouting-for-indoorgml/
├── data/
│   └── sample-PUN-IT-2026-05-06.gml   # PUN-IT 样本
├── sql/
│   ├── IndoorGML_core.sql            # IndoorGML Core DDL（先执行）
│   ├── IndoorGML_navi.sql            # IndoorGML Navigation DDL
│   ├── install.sql                   # 安装 routing：core.sql + qgis.sql
│   ├── core.sql                      # routing schema + API
│   ├── qgis.sql                      # path_params + v_qgis_* 视图
│   └── punit_shortest_path.sql       # PUN-IT 最短路径 demo
├── scripts/
│   ├── setup_db.sh                   # 建库 + schema + 导入 GML + routing
│   └── install.sh                    # 仅安装 / 刷新 routing
├── tools/
│   ├── import_gml.py                 # GML → PostgreSQL
│   ├── db_geometry.py                # 几何转换
│   ├── db_graph.py                   # connects jsonb helpers
│   └── db_names.py                   # 库名与连接默认值
├── qgis/                             # QGIS 工程与出图
├── requirements.txt
└── README.md
```

## Architecture

```text
data/*.gml  →  tools/import_gml.py  →  IndoorGML Node/Edge
                                              ↓
                                    routing.refresh_network()
                                              ↓
                                     node_map / edge_map
                                              ↓
                                    pgr_dijkstra (shortest_path)
                                              ↓
                              Route 表  ·  QGIS 视图 (path_params)
```

拓扑来自 `Edge.connects` + cost，不使用已弃用的 `pgr_createTopology`。

| Object | Role |
|--------|------|
| `routing.node_map` | 整数 `vid` ↔ IndoorGML `NodeID` |
| `routing.edge_map` | `source` / `target` / `cost` |
| `routing.refresh_network()` | 从 Node/Edge 重建网络（`length` 或 `weight`） |
| `routing.shortest_path()` | 两点 Dijkstra |
| `routing.save_route()` | 写入 IndoorGML `"Route"` |
| `routing.path_params` / `v_qgis_*` | QGIS 起终点与图层 |

## Prerequisites

- PostgreSQL + PostGIS
- pgRouting ≥ 3.4（测试于 3.8）
- Python 3.10+（仅 GML 导入需要）

```bash
pip install -r requirements.txt
```

## Quick start

一键：建库 → IndoorGML schema → 导入 PUN-IT → 安装 routing → 跑 demo：

```bash
cd pgrouting-for-indoorgml
./scripts/setup_db.sh -d indoorgml_punit --recreate --demo
```

库已有 IndoorGML 数据时，只装 routing：

```bash
./scripts/install.sh -d indoorgml_punit --refresh --demo
```

或纯 `psql`：

```bash
psql -h localhost -U postgres -d indoorgml_punit -f sql/install.sql
psql -h localhost -U postgres -d indoorgml_punit -f sql/punit_shortest_path.sql
```

手动建库步骤：

```bash
# 1. IndoorGML schema
psql -d indoorgml_punit -f sql/IndoorGML_core.sql
psql -d indoorgml_punit -f sql/IndoorGML_navi.sql

# 2. 导入 GML
python3 tools/import_gml.py --db indoorgml_punit --no-schema

# 3. routing + demo
./scripts/install.sh -d indoorgml_punit --demo
```

## Usage

```sql
SELECT * FROM routing.refresh_network('length', 'DS1');
SELECT * FROM routing.shortest_path('node_new_17', 'node_new_6');
SELECT routing.save_route('route_demo_01', 'node_new_17', 'node_new_6');

-- QGIS 起终点
SELECT * FROM routing.set_path_endpoints('node_new_17', 'node_new_6');
```

代价模式：`length`（默认，几何长度）· `weight`（`Edge.Weight`）。

Demo 路径：`node_new_17` → `node_new_6`（length 约 3268.94）。

## QGIS

```bash
open -a "QGIS-final-3_44_5" qgis/IndoorGML_PUN_IT_pgRouting.qgz
```

详见 [qgis/README.md](qgis/README.md)。

## Notes

- 默认无向（`reverse_cost = cost`，`directed := false`）。
- 多层：省略 layer 过滤，或在 Dijkstra 前加入 interlayer 边。
