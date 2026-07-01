"""
Table role classification based on naming, schema shape, and lineage position.

Roles:
  entity      — root fact tables representing business objects (Customer, Order)
  fact        — transactional / event tables with FK refs to entities
  junction    — bridge tables expressing many:many relationships
  aggregation — pre-computed summary / reporting tables
  staging     — intermediate / temp tables in a transformation pipeline
  unknown     — cannot be classified with confidence
"""
from __future__ import annotations

# ── Name prefix/suffix patterns ───────────────────────────────────────────────

_STAGING_PREFIXES   = {"stg_", "staging_", "tmp_", "temp_", "raw_", "src_", "landing_", "bronze_"}
_DIMENSION_PREFIXES = {"dim_", "d_"}
_FACT_PREFIXES      = {"fact_", "fct_", "f_"}
_AGG_SUFFIXES       = {
    "_agg", "_aggregated", "_summary", "_report",
    "_metrics", "_stats", "_kpi", "_rollup", "_daily",
    "_weekly", "_monthly", "_yearly",
}
_JUNCTION_SUFFIXES  = {"_map", "_mapping", "_xref", "_bridge", "_link", "_rel", "_assoc", "_pivot"}

# Column names that strongly suggest a primary key
_PK_PATTERNS = {"id", "pk", "key", "uuid", "guid"}
# Column name endings that suggest a foreign key
_FK_SUFFIXES = ("_id", "_pk", "_key", "_fk", "_ref", "_uuid")


def _name_lower(table_name: str) -> str:
    """Extract bare table name (no catalog/schema) and lowercase it."""
    return table_name.split(".")[-1].lower()


def _starts_with_any(name: str, prefixes: set[str]) -> bool:
    return any(name.startswith(p) for p in prefixes)


def _ends_with_any(name: str, suffixes: set | tuple) -> bool:
    return any(name.endswith(s) for s in suffixes)


def count_fk_columns(columns: list[dict]) -> int:
    """Count columns that look like foreign keys."""
    return sum(
        1 for c in columns
        if c.get("name", "").lower() != "id"
        and _ends_with_any(c.get("name", "").lower(), _FK_SUFFIXES)
    )


def has_primary_key(columns: list[dict]) -> bool:
    """True if there's a column that looks like a primary key."""
    names = {c.get("name", "").lower() for c in columns}
    return bool(names & _PK_PATTERNS) or any(
        n == "id" or _ends_with_any(n, ("_id",)) and len(n) <= 10
        for n in names
    )


def classify_table(
    full_name: str,
    columns: list[dict],
    n_upstream: int = 0,
    n_downstream: int = 0,
) -> tuple[str, float]:
    """
    Classify a table's role.

    Returns (role: str, confidence: float).

    confidence is in [0.0, 1.0]:
      >= 0.85 → strong signal (name prefix, junction shape)
      0.60–0.84 → moderate signal (position in lineage + shape)
      < 0.60 → weak / unknown
    """
    name = _name_lower(full_name)
    n_cols = len(columns)
    n_fk = count_fk_columns(columns)
    has_pk = has_primary_key(columns)

    # ── Staging ──
    if _starts_with_any(name, _STAGING_PREFIXES):
        return "staging", 0.90

    # ── Aggregation ──
    if _ends_with_any(name, _AGG_SUFFIXES):
        return "aggregation", 0.90
    if _starts_with_any(name, _FACT_PREFIXES) and n_upstream > 0:
        return "aggregation", 0.75

    # ── Dimension / Entity ──
    if _starts_with_any(name, _DIMENSION_PREFIXES):
        return "entity", 0.90

    # ── Junction ──
    if _ends_with_any(name, _JUNCTION_SUFFIXES):
        return "junction", 0.88
    if n_cols >= 2 and n_fk >= 2 and n_fk / max(n_cols, 1) >= 0.6:
        # Mostly FK columns → junction/bridge table
        return "junction", 0.80

    # ── Entity ──
    # Root source with a PK and meaningful columns
    if n_upstream == 0 and has_pk and n_cols >= 3:
        return "entity", 0.82
    if n_upstream == 0 and n_cols >= 5:
        return "entity", 0.65

    # ── Fact ──
    # Has upstream (transformed from somewhere) + FK columns
    if n_upstream >= 1 and n_fk >= 1 and n_downstream >= 1:
        return "fact", 0.70
    if n_upstream >= 1 and n_fk >= 2:
        return "fact", 0.65

    # ── Aggregation by position ──
    if n_upstream >= 2 and n_downstream == 0:
        return "aggregation", 0.60

    return "unknown", 0.40


def classify_all(
    tables: dict,   # {full_name: {"columns": [...], "role": ...}}
    upstream_counts: dict[str, int],
    downstream_counts: dict[str, int],
) -> dict[str, tuple[str, float]]:
    """
    Classify every table in the graph.

    Returns {full_name: (role, confidence)}.
    """
    return {
        name: classify_table(
            name,
            info.get("columns", []),
            upstream_counts.get(name, 0),
            downstream_counts.get(name, 0),
        )
        for name, info in tables.items()
    }
