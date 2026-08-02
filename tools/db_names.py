"""
IndoorGML PostgreSQL 数据库命名（含数据源缩写）

缩写说明:
  pnu201 - input/PNU-201.json
  punit  - data/sample-PUN-IT-2026-05-06.gml
  route  - Route 表（routeNode / routeEdge JSON 属性）测试扩展
  test   - 综合测试库（Route + InterLayerConnection + TL2）
"""

# 当前标准库名
DB_PNU201 = "indoorgml_pnu201"
DB_PUNIT = "indoorgml_punit"
DB_PUNIT_ROUTE = "indoorgml_punit_route"
DB_PNU201_TEST = "indoorgml_pnu201_test"

# 旧名 -> 新名（历史迁移对照）
LEGACY_DB_NAMES = {
    "indoorgml2": DB_PNU201,
    "indoorgml3": DB_PUNIT,
    "indoorgml_route_test": DB_PUNIT_ROUTE,
    "indoorgml_test": DB_PNU201_TEST,
    # 上一轮缩写
    "indoorgml_pnu": DB_PNU201,
    "indoorgml_gml": DB_PUNIT,
    "indoorgml_gml_route": DB_PUNIT_ROUTE,
    "indoorgml_pnu_test": DB_PNU201_TEST,
}

DEFAULT_PG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "postgres",
}
