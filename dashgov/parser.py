"""
SQL-based lineage extraction.

Parses CREATE TABLE AS SELECT, INSERT INTO SELECT, and plain SELECT
statements to extract table-level and column-level lineage without
requiring a live Unity Catalog connection.

Requires sqlglot (pure Python, no Spark dependency).
"""
from __future__ import annotations


def _sqlglot():
    try:
        import sqlglot
        return sqlglot
    except ImportError:
        raise RuntimeError("sqlglot is required: pip install sqlglot")


def parse_table_lineage(sql: str, dialect: str = "spark") -> dict:
    """
    Extract table-level lineage from a SQL statement.

    Returns:
        {
          "target": str | None,       # the table being written to
          "sources": [str, ...],      # tables being read from
          "type": "ctas"|"insert"|"select"|"unknown"
        }
    """
    sg = _sqlglot()
    exp = sg.exp

    try:
        stmt = sg.parse_one(sql, dialect=dialect)
    except Exception:
        return {"target": None, "sources": [], "type": "unknown"}

    def _full(tbl) -> str:
        parts = [p for p in (tbl.catalog, tbl.db, tbl.name) if p]
        return ".".join(parts) if parts else (tbl.name or "")

    def _table_names(node) -> list[str]:
        return [_full(t) for t in node.find_all(exp.Table) if t.name]

    if isinstance(stmt, exp.Create):
        tbl = stmt.find(exp.Table)
        target_full = _full(tbl) if tbl else None
        target_short = tbl.name if tbl else None
        all_names = _table_names(stmt)
        sources = [n for n in all_names if n != target_full]
        return {"target": target_short, "sources": list(dict.fromkeys(sources)), "type": "ctas"}

    if isinstance(stmt, exp.Insert):
        tbl = stmt.find(exp.Table)
        target_full = _full(tbl) if tbl else None
        target_short = tbl.name if tbl else None
        inner = stmt.find(sg.exp.Select)
        if inner:
            sources = [n for n in _table_names(inner) if n != target_full]
        else:
            sources = []
        return {"target": target_short, "sources": list(dict.fromkeys(sources)), "type": "insert"}

    if isinstance(stmt, (exp.Select, exp.Subquery)):
        sources = list(dict.fromkeys(_table_names(stmt)))
        return {"target": None, "sources": sources, "type": "select"}

    return {"target": None, "sources": [], "type": "unknown"}


def parse_column_lineage(
    sql: str,
    target_table: str,
    dialect: str = "spark",
) -> list[dict]:
    """
    Extract column-level lineage from a SQL statement.

    Returns list of:
        {
          "target_column": str,
          "source_table": str | None,
          "source_column": str | None,
          "expression": str | None,   # for computed columns
        }

    Only handles direct column references. Complex expressions
    (aggregations, UDFs) are returned with expression set to the SQL text.
    """
    sg = _sqlglot()
    exp = sg.exp

    try:
        stmt = sg.parse_one(sql, dialect=dialect)
    except Exception:
        return []

    # Unwrap CREATE TABLE AS SELECT / INSERT INTO SELECT
    select = stmt.find(exp.Select)
    if select is None:
        if isinstance(stmt, exp.Select):
            select = stmt
        else:
            return []

    # Build alias map: alias → real table name
    alias_map: dict[str, str] = {}
    for from_expr in select.find_all(exp.From):
        tbl = from_expr.find(exp.Table)
        if tbl:
            alias_map[tbl.alias or tbl.name] = tbl.name

    for join in select.find_all(exp.Join):
        tbl = join.find(exp.Table)
        if tbl:
            alias_map[tbl.alias or tbl.name] = tbl.name

    result = []
    for sel in select.selects:
        alias = sel.alias or (sel.name if hasattr(sel, "name") else None)
        target_col = alias or str(sel)

        if isinstance(sel, (exp.Column, exp.Alias)):
            col_node = sel.find(exp.Column) if isinstance(sel, exp.Alias) else sel
            if col_node:
                tbl_alias = (
                    col_node.table if hasattr(col_node, "table") else None
                )
                src_tbl = alias_map.get(tbl_alias, tbl_alias) if tbl_alias else None
                src_col = col_node.name if hasattr(col_node, "name") else None
                result.append({
                    "target_table": target_table,
                    "target_column": target_col,
                    "source_table": src_tbl,
                    "source_column": src_col,
                    "expression": None,
                })
            else:
                result.append({
                    "target_table": target_table,
                    "target_column": target_col,
                    "source_table": None,
                    "source_column": None,
                    "expression": str(sel),
                })
        else:
            result.append({
                "target_table": target_table,
                "target_column": target_col,
                "source_table": None,
                "source_column": None,
                "expression": str(sel),
            })

    return result


def parse_notebook_lineage(sql_cells: list[str], dialect: str = "spark") -> dict:
    """
    Parse multiple SQL cells from a notebook and build combined lineage.

    Returns:
        {
          "table_edges": [{"source": str, "target": str}, ...],
          "column_edges": [{...}, ...],
          "statements": int,
          "parsed": int,
        }
    """
    table_edges: list[dict] = []
    column_edges: list[dict] = []
    parsed = 0

    for cell in sql_cells:
        cell = cell.strip()
        if not cell:
            continue
        tl = parse_table_lineage(cell, dialect=dialect)
        if tl["target"] and tl["sources"]:
            parsed += 1
            for src in tl["sources"]:
                table_edges.append({"source": src, "target": tl["target"]})
            cl = parse_column_lineage(cell, tl["target"], dialect=dialect)
            for ce in cl:
                if ce["source_table"] and ce["source_column"]:
                    column_edges.append({
                        "source_table": ce["source_table"],
                        "source_column": ce["source_column"],
                        "target_table": ce["target_table"],
                        "target_column": ce["target_column"],
                        "transformation": ce.get("expression"),
                    })

    return {
        "table_edges": table_edges,
        "column_edges": column_edges,
        "statements": len(sql_cells),
        "parsed": parsed,
    }
