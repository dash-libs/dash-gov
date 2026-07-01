"""Tests for SQL-based lineage extraction (no Spark, no UC required)."""
import pytest
from dashgov.parser import parse_table_lineage, parse_column_lineage, parse_notebook_lineage


# ── parse_table_lineage ──────────────────────────────────────────────────────

def test_ctas_single_source():
    sql = "CREATE TABLE gold.orders AS SELECT * FROM silver.orders"
    r = parse_table_lineage(sql)
    assert r["type"] == "ctas"
    assert r["target"] == "orders"
    assert "orders" in r["sources"] or "silver" in str(r["sources"])


def test_ctas_multi_source_join():
    sql = """
    CREATE TABLE gold.order_summary AS
    SELECT o.id, c.name
    FROM silver.orders o
    JOIN silver.customers c ON o.customer_id = c.id
    """
    r = parse_table_lineage(sql)
    assert r["type"] == "ctas"
    assert r["target"] == "order_summary"
    assert len(r["sources"]) >= 2


def test_insert_into_select():
    sql = "INSERT INTO gold.orders SELECT * FROM silver.orders WHERE status = 'active'"
    r = parse_table_lineage(sql)
    assert r["type"] == "insert"
    assert r["target"] == "orders"
    assert len(r["sources"]) >= 1


def test_plain_select_no_target():
    sql = "SELECT a.col1, b.col2 FROM table_a a JOIN table_b b ON a.id = b.id"
    r = parse_table_lineage(sql)
    assert r["type"] == "select"
    assert r["target"] is None
    assert "table_a" in r["sources"] or "table_b" in r["sources"]


def test_unknown_statement():
    sql = "DROP TABLE my_table"
    r = parse_table_lineage(sql)
    assert r["target"] is None


def test_invalid_sql_returns_gracefully():
    sql = "THIS IS NOT VALID SQL @@@ !!!"
    r = parse_table_lineage(sql)
    assert r["type"] in ("unknown", "select", "ctas", "insert")


def test_snowflake_dialect():
    sql = "CREATE TABLE target AS SELECT col FROM source"
    r = parse_table_lineage(sql, dialect="snowflake")
    assert r["target"] == "target"


def test_sources_deduped():
    sql = """
    CREATE TABLE t AS
    SELECT a.x, a.y FROM src a WHERE a.x > (SELECT max(x) FROM src)
    """
    r = parse_table_lineage(sql)
    sources = r["sources"]
    assert sources.count("src") <= 1


# ── parse_column_lineage ─────────────────────────────────────────────────────

def test_column_lineage_direct_select():
    sql = "CREATE TABLE gold.t AS SELECT a.customer_id, a.amount FROM silver.orders a"
    cols = parse_column_lineage(sql, "t")
    names = [c["target_column"] for c in cols]
    assert "customer_id" in names
    assert "amount" in names


def test_column_lineage_source_table_mapped():
    sql = """
    CREATE TABLE g AS
    SELECT o.id, c.email
    FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    cols = parse_column_lineage(sql, "g")
    # At least some columns should have a resolved source_table
    resolved = [c for c in cols if c["source_table"] is not None]
    assert len(resolved) >= 1


def test_column_lineage_expression_captured():
    sql = "CREATE TABLE t AS SELECT a + b AS total FROM src"
    cols = parse_column_lineage(sql, "t")
    assert any(c["target_column"] == "total" for c in cols)


def test_column_lineage_empty_on_bad_sql():
    cols = parse_column_lineage("NOT VALID", "t")
    assert isinstance(cols, list)


# ── parse_notebook_lineage ───────────────────────────────────────────────────

def test_notebook_lineage_multiple_cells():
    cells = [
        "CREATE TABLE silver.orders AS SELECT * FROM bronze.raw_orders",
        "CREATE TABLE gold.summary AS SELECT customer_id, SUM(amount) FROM silver.orders GROUP BY 1",
    ]
    r = parse_notebook_lineage(cells)
    assert r["parsed"] == 2
    assert r["statements"] == 2
    sources = {e["source"] for e in r["table_edges"]}
    targets = {e["target"] for e in r["table_edges"]}
    assert len(sources) >= 1
    assert len(targets) >= 1


def test_notebook_lineage_skips_empty_cells():
    cells = ["", "  ", "CREATE TABLE t AS SELECT * FROM s"]
    r = parse_notebook_lineage(cells)
    assert r["statements"] == 3
    assert r["parsed"] == 1


def test_notebook_lineage_plain_select_not_counted():
    cells = ["SELECT * FROM my_table"]
    r = parse_notebook_lineage(cells)
    assert r["parsed"] == 0
    assert r["table_edges"] == []
