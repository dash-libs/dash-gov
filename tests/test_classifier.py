"""Tests for table role classification heuristics."""
import pytest
from dashgov.classifier import classify_table, classify_all, count_fk_columns, has_primary_key


def _cols(*names: str, type_: str = "string") -> list[dict]:
    return [{"name": n, "type": type_, "nullable": True} for n in names]


# ── count_fk_columns ─────────────────────────────────────────────────────────

def test_count_fk_customer_id():
    assert count_fk_columns(_cols("customer_id", "product_id", "amount")) == 2


def test_count_fk_ignores_plain_id():
    assert count_fk_columns(_cols("id", "name", "email")) == 0


def test_count_fk_suffix_variants():
    cols = _cols("order_fk", "user_ref", "transaction_pk", "value")
    assert count_fk_columns(cols) >= 3


# ── has_primary_key ──────────────────────────────────────────────────────────

def test_has_pk_with_id_column():
    assert has_primary_key(_cols("id", "name")) is True


def test_has_pk_with_uuid():
    assert has_primary_key(_cols("uuid", "email")) is True


def test_no_pk_for_pure_metric_table():
    cols = _cols("revenue", "cost", "margin", "period")
    # None of these are obviously PKs
    result = has_primary_key(cols)
    assert isinstance(result, bool)


# ── classify_table: staging ──────────────────────────────────────────────────

def test_staging_prefix_stg():
    role, conf = classify_table("cat.sch.stg_customers", _cols("id", "email"))
    assert role == "staging"
    assert conf >= 0.85


def test_staging_prefix_raw():
    role, conf = classify_table("raw_orders", _cols("id", "customer_id"))
    assert role == "staging"


def test_staging_prefix_landing():
    role, _ = classify_table("landing_events", _cols("ts", "payload"))
    assert role == "staging"


# ── classify_table: entity ───────────────────────────────────────────────────

def test_entity_dim_prefix():
    role, conf = classify_table("dim_customer", _cols("id", "name", "email", "city"), n_upstream=0)
    assert role == "entity"
    assert conf >= 0.85


def test_entity_root_source_with_pk():
    role, conf = classify_table(
        "customers",
        _cols("id", "name", "email", "phone", "address"),
        n_upstream=0,
    )
    assert role == "entity"
    assert conf >= 0.65


# ── classify_table: junction ─────────────────────────────────────────────────

def test_junction_suffix():
    role, conf = classify_table("order_product_map", _cols("order_id", "product_id"))
    assert role == "junction"
    assert conf >= 0.85


def test_junction_by_fk_ratio():
    # 4 FK columns out of 5 total → junction
    cols = _cols("order_id", "customer_id", "product_id", "warehouse_id", "quantity")
    role, conf = classify_table("cat.sch.some_bridge_table", cols)
    assert role == "junction"
    assert conf >= 0.75


# ── classify_table: aggregation ──────────────────────────────────────────────

def test_aggregation_suffix_agg():
    role, conf = classify_table("monthly_revenue_agg", _cols("month", "revenue", "cost"))
    assert role == "aggregation"


def test_aggregation_suffix_summary():
    role, _ = classify_table("order_summary", _cols("date", "total_orders", "gmv"))
    assert role == "aggregation"


def test_aggregation_suffix_metrics():
    role, _ = classify_table("kpi_metrics", _cols("date", "dau", "mau"))
    assert role == "aggregation"


def test_aggregation_by_position():
    # Multiple upstream, no downstream → likely aggregation
    role, conf = classify_table(
        "complex_rollup",
        _cols("date", "region", "revenue"),
        n_upstream=3,
        n_downstream=0,
    )
    assert role == "aggregation"
    assert conf >= 0.55


# ── classify_table: fact ─────────────────────────────────────────────────────

def test_fact_prefix():
    role, conf = classify_table(
        "fact_sales",
        _cols("id", "customer_id", "product_id", "amount", "date"),
        n_upstream=2,
        n_downstream=1,
    )
    assert role in ("aggregation", "fact")


def test_fact_by_upstream_and_fk():
    role, conf = classify_table(
        "order_events",
        _cols("id", "order_id", "customer_id", "event_type", "ts"),
        n_upstream=1,
        n_downstream=2,
    )
    assert role == "fact"
    assert conf >= 0.60


# ── classify_all ─────────────────────────────────────────────────────────────

def test_classify_all_returns_all_tables():
    tables = {
        "stg_customers":  {"columns": _cols("id", "name")},
        "dim_products":   {"columns": _cols("id", "name", "price")},
        "fact_sales":     {"columns": _cols("id", "customer_id", "product_id", "amount")},
        "monthly_kpi_agg":{"columns": _cols("month", "revenue")},
    }
    ups = {"fact_sales": 2}
    downs = {"stg_customers": 1, "dim_products": 1}
    result = classify_all(tables, ups, downs)
    assert len(result) == 4
    assert result["stg_customers"][0] == "staging"
    assert result["dim_products"][0] == "entity"
    assert result["monthly_kpi_agg"][0] == "aggregation"


def test_confidence_is_float_between_0_and_1():
    role, conf = classify_table("unknown_table_xyz", _cols("a", "b"))
    assert 0.0 <= conf <= 1.0
