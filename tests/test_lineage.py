"""Tests for LineageGraph — traversal, impact analysis, column lineage."""
from dashgov.lineage import build_lineage_graph, LineageGraph


def _make_graph() -> LineageGraph:
    """
    raw_customers ──► silver_customers ──► gold_customers
                                           ▲
    raw_orders ──────► silver_orders ──────┘
                            │
                            ▼
                       gold_orders
    """
    tables = [
        {"full_name": "cat.raw.raw_customers",     "columns": [{"name": "id", "type": "bigint"}, {"name": "email", "type": "string"}]},
        {"full_name": "cat.raw.raw_orders",         "columns": [{"name": "id", "type": "bigint"}, {"name": "customer_id", "type": "bigint"}]},
        {"full_name": "cat.silver.silver_customers","columns": [{"name": "id", "type": "bigint"}, {"name": "email", "type": "string"}]},
        {"full_name": "cat.silver.silver_orders",   "columns": [{"name": "id", "type": "bigint"}, {"name": "customer_id", "type": "bigint"}]},
        {"full_name": "cat.gold.gold_customers",    "columns": [{"name": "id", "type": "bigint"}]},
        {"full_name": "cat.gold.gold_orders",       "columns": [{"name": "id", "type": "bigint"}]},
    ]
    table_edges = [
        {"source": "cat.raw.raw_customers",     "target": "cat.silver.silver_customers"},
        {"source": "cat.raw.raw_orders",         "target": "cat.silver.silver_orders"},
        {"source": "cat.silver.silver_customers","target": "cat.gold.gold_customers"},
        {"source": "cat.silver.silver_orders",   "target": "cat.gold.gold_customers"},
        {"source": "cat.silver.silver_orders",   "target": "cat.gold.gold_orders"},
    ]
    column_edges = [
        {"source_table": "cat.raw.raw_customers", "source_column": "id",    "target_table": "cat.silver.silver_customers", "target_column": "id"},
        {"source_table": "cat.raw.raw_customers", "source_column": "email", "target_table": "cat.silver.silver_customers", "target_column": "email"},
        {"source_table": "cat.raw.raw_orders",    "source_column": "customer_id", "target_table": "cat.silver.silver_orders", "target_column": "customer_id"},
    ]
    return build_lineage_graph(tables, table_edges, column_edges)


# ── Construction ─────────────────────────────────────────────────────────────

def test_build_graph_table_count():
    g = _make_graph()
    assert len(g.tables) == 6


def test_build_graph_edge_count():
    g = _make_graph()
    assert len(g.table_edges) == 5
    assert len(g.column_edges) == 3


def test_table_node_fields_parsed():
    g = _make_graph()
    node = g.tables["cat.silver.silver_customers"]
    assert node.catalog == "cat"
    assert node.schema_name == "silver"
    assert node.table == "silver_customers"


# ── Upstream / downstream ────────────────────────────────────────────────────

def test_upstream_depth_1():
    g = _make_graph()
    up = g.upstream_tables("cat.silver.silver_customers", depth=1)
    assert "cat.raw.raw_customers" in up
    assert len(up) == 1


def test_upstream_depth_2():
    g = _make_graph()
    up = g.upstream_tables("cat.gold.gold_customers", depth=2)
    assert "cat.silver.silver_customers" in up
    assert "cat.silver.silver_orders" in up


def test_downstream_depth_1():
    g = _make_graph()
    down = g.downstream_tables("cat.raw.raw_orders", depth=1)
    assert "cat.silver.silver_orders" in down


def test_downstream_depth_2():
    g = _make_graph()
    down = g.downstream_tables("cat.raw.raw_orders", depth=2)
    assert "cat.gold.gold_customers" in down
    assert "cat.gold.gold_orders" in down


def test_no_upstream_for_root():
    g = _make_graph()
    assert g.upstream_tables("cat.raw.raw_customers") == []


def test_no_downstream_for_leaf():
    g = _make_graph()
    assert g.downstream_tables("cat.gold.gold_orders") == []


# ── Root sources ─────────────────────────────────────────────────────────────

def test_root_sources_for_gold_customers():
    g = _make_graph()
    roots = g.root_sources("cat.gold.gold_customers")
    assert "cat.raw.raw_customers" in roots
    assert "cat.raw.raw_orders" in roots


def test_root_sources_for_raw_table_is_empty():
    g = _make_graph()
    assert g.root_sources("cat.raw.raw_customers") == []


# ── Impact analysis ──────────────────────────────────────────────────────────

def test_impact_analysis_direct_dependents():
    g = _make_graph()
    imp = g.impact_analysis("cat.silver.silver_orders")
    assert "cat.gold.gold_customers" in imp["direct_dependents"]
    assert "cat.gold.gold_orders" in imp["direct_dependents"]


def test_impact_analysis_total_count():
    g = _make_graph()
    imp = g.impact_analysis("cat.raw.raw_customers")
    assert imp["total_affected_tables"] >= 2


def test_impact_analysis_affected_columns():
    g = _make_graph()
    imp = g.impact_analysis("cat.raw.raw_customers")
    assert "id" in imp["affected_column_paths"]
    assert "email" in imp["affected_column_paths"]


# ── Column lineage ───────────────────────────────────────────────────────────

def test_column_sources():
    g = _make_graph()
    sources = g.column_sources("cat.silver.silver_customers", "email")
    assert len(sources) == 1
    assert sources[0].source_table == "cat.raw.raw_customers"
    assert sources[0].source_column == "email"


def test_column_targets():
    g = _make_graph()
    targets = g.column_targets("cat.raw.raw_orders", "customer_id")
    assert len(targets) == 1
    assert targets[0].target_table == "cat.silver.silver_orders"


def test_column_lineage_chain():
    g = _make_graph()
    chain = g.column_lineage_chain("cat.silver.silver_customers", "id")
    assert any(u["table"] == "cat.raw.raw_customers" for u in chain["upstream_columns"])


def test_column_sources_empty_for_unknown():
    g = _make_graph()
    assert g.column_sources("cat.gold.gold_orders", "id") == []


# ── Summary / export ─────────────────────────────────────────────────────────

def test_summary_root_and_leaf():
    g = _make_graph()
    s = g.summary()
    assert "cat.raw.raw_customers" in s["root_sources"]
    assert "cat.raw.raw_orders" in s["root_sources"]
    assert "cat.gold.gold_customers" in s["leaf_sinks"]
    assert "cat.gold.gold_orders" in s["leaf_sinks"]


def test_to_dict_roundtrip():
    g = _make_graph()
    d = g.to_dict()
    assert "tables" in d
    assert "table_edges" in d
    assert "column_edges" in d
    assert len(d["tables"]) == 6


def test_build_from_minimal_table_entry():
    g = build_lineage_graph(
        tables=[{"full_name": "a.b.c", "columns": []}],
        table_edges=[],
        column_edges=[],
    )
    assert "a.b.c" in g.tables
    assert g.tables["a.b.c"].table == "c"
