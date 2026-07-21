"""Tests for the pure-Python parts of the governance scanner (no Spark)."""
from dashgov.scanner import detect_pii_in_values, infer_sensitivity, luhn_valid


# ── Luhn ─────────────────────────────────────────────────────────────────────

def test_luhn_accepts_valid_card():
    assert luhn_valid("4539578763621486")  # valid Visa test number


def test_luhn_rejects_arbitrary_digits():
    assert not luhn_valid("1234567890123456")


# ── PII detection ────────────────────────────────────────────────────────────

def test_detects_email():
    assert detect_pii_in_values(["alice@example.com"]) == ["email"]


def test_credit_card_requires_luhn():
    # 16 digits that fail Luhn (an account number) must NOT be flagged as a card
    assert "credit_card" not in detect_pii_in_values(["1234567890123456"])
    assert "credit_card" in detect_pii_in_values(["4539 5787 6362 1486"])


def test_detects_ssn():
    assert "ssn" in detect_pii_in_values(["123-45-6789"])


def test_empty_values_detect_nothing():
    assert detect_pii_in_values([]) == []
    assert detect_pii_in_values([None, ""]) == []


# ── Sensitivity ──────────────────────────────────────────────────────────────

def test_sensitivity_levels():
    assert infer_sensitivity("annual_salary") == "HIGH"
    assert infer_sensitivity("email_address") == "MEDIUM"
    assert infer_sensitivity("city") == "LOW"
    assert infer_sensitivity("quantity") == "NONE"


# ── LineageGraph JSON round-trip ─────────────────────────────────────────────

def test_lineage_graph_to_json_roundtrip():
    from dashgov.lineage import LineageGraph, build_lineage_graph

    g = build_lineage_graph(
        tables=[{"full_name": "c.s.a", "columns": [{"name": "id"}]},
                {"full_name": "c.s.b", "columns": []}],
        table_edges=[{"source": "c.s.a", "target": "c.s.b"}],
        column_edges=[{"source_table": "c.s.a", "source_column": "id",
                       "target_table": "c.s.b", "target_column": "a_id"}],
    )
    g2 = LineageGraph.from_json(g.to_json())
    assert set(g2.tables) == {"c.s.a", "c.s.b"}
    assert g2.downstream_tables("c.s.a") == ["c.s.b"]
    assert len(g2.column_edges) == 1
