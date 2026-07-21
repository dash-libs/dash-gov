"""
Lineage graph — table-level and column-level data lineage.

Works with plain Python dicts so it is fully testable without Spark or UC.
Use fetch_uc_lineage() to pull live data from a Unity Catalog workspace.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
from typing import Optional


@dataclass
class TableNode:
    full_name: str          # catalog.schema.table
    catalog: str
    schema_name: str
    table: str
    columns: list[dict]     # [{"name": str, "type": str, "nullable": bool}]
    role: str = "unknown"   # entity | fact | junction | aggregation | staging | unknown


@dataclass
class TableEdge:
    source: str   # full table name
    target: str   # full table name


@dataclass
class ColumnEdge:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    transformation: Optional[str] = None  # SQL expression when known


class LineageGraph:
    """Directed acyclic graph of table and column lineage."""

    def __init__(
        self,
        tables: dict[str, TableNode],
        table_edges: list[TableEdge],
        column_edges: list[ColumnEdge],
    ):
        self.tables = tables
        self.table_edges = table_edges
        self.column_edges = column_edges

        # adjacency: source → {targets}
        self._downstream: dict[str, set[str]] = {}
        self._upstream: dict[str, set[str]] = {}
        for e in table_edges:
            self._downstream.setdefault(e.source, set()).add(e.target)
            self._upstream.setdefault(e.target, set()).add(e.source)

    # ── Table-level traversal ────────────────────────────────────────────────

    def upstream_tables(self, table: str, depth: int = 1) -> list[str]:
        """All tables that feed into *table*, up to *depth* hops."""
        return self._bfs(table, self._upstream, depth)

    def downstream_tables(self, table: str, depth: int = 1) -> list[str]:
        """All tables that consume from *table*, up to *depth* hops."""
        return self._bfs(table, self._downstream, depth)

    def root_sources(self, table: str) -> list[str]:
        """Tables with no upstream that eventually feed into *table*."""
        visited, result = set(), []
        stack = [table]
        while stack:
            t = stack.pop()
            if t in visited:
                continue
            visited.add(t)
            ups = list(self._upstream.get(t, []))
            if not ups and t != table:
                result.append(t)
            stack.extend(ups)
        return sorted(result)

    def impact_analysis(self, table: str) -> dict:
        """What breaks if *table* changes — full downstream tree."""
        direct = sorted(self._downstream.get(table, []))
        all_downstream = self._bfs(table, self._downstream, depth=999)
        col_targets = {}
        for ce in self.column_edges:
            if ce.source_table == table:
                col_targets.setdefault(ce.source_column, []).append(
                    f"{ce.target_table}.{ce.target_column}"
                )
        return {
            "table": table,
            "direct_dependents": direct,
            "all_downstream": all_downstream,
            "affected_column_paths": col_targets,
            "total_affected_tables": len(all_downstream),
        }

    # ── Column-level traversal ───────────────────────────────────────────────

    def column_sources(self, table: str, column: str) -> list[ColumnEdge]:
        """Edges that feed into *table.column*."""
        return [
            e for e in self.column_edges
            if e.target_table == table and e.target_column == column
        ]

    def column_targets(self, table: str, column: str) -> list[ColumnEdge]:
        """Edges that *table.column* feeds into."""
        return [
            e for e in self.column_edges
            if e.source_table == table and e.source_column == column
        ]

    def column_lineage_chain(self, table: str, column: str) -> dict:
        """Full upstream chain for a single column."""
        visited, upstream = set(), []
        stack = [(table, column)]
        while stack:
            t, c = stack.pop()
            key = f"{t}.{c}"
            if key in visited:
                continue
            visited.add(key)
            for src in self.column_sources(t, c):
                upstream.append({"table": src.source_table, "column": src.source_column})
                stack.append((src.source_table, src.source_column))
        return {
            "table": table,
            "column": column,
            "upstream_columns": upstream,
        }

    # ── Export ───────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "tables": {
                k: {
                    "full_name": v.full_name,
                    "catalog": v.catalog,
                    "schema_name": v.schema_name,
                    "table": v.table,
                    "columns": v.columns,
                    "role": v.role,
                }
                for k, v in self.tables.items()
            },
            "table_edges": [
                {"source": e.source, "target": e.target} for e in self.table_edges
            ],
            "column_edges": [
                {
                    "source_table": e.source_table,
                    "source_column": e.source_column,
                    "target_table": e.target_table,
                    "target_column": e.target_column,
                    "transformation": e.transformation,
                }
                for e in self.column_edges
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        """JSON form of to_dict() — what dashontology's UI asks users to paste."""
        import json
        return json.dumps(self.to_dict(), indent=indent)

    @staticmethod
    def from_json(text: str) -> "LineageGraph":
        import json
        data = json.loads(text)
        return build_lineage_graph(
            list(data.get("tables", {}).values()) if isinstance(data.get("tables"), dict)
            else data.get("tables", []),
            data.get("table_edges", []),
            data.get("column_edges", []),
        )

    def summary(self) -> dict:
        return {
            "total_tables": len(self.tables),
            "total_table_edges": len(self.table_edges),
            "total_column_edges": len(self.column_edges),
            "root_sources": [t for t in self.tables if t not in self._upstream],
            "leaf_sinks": [t for t in self.tables if t not in self._downstream],
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _bfs(self, start: str, adj: dict, depth: int) -> list[str]:
        visited, result = {start}, []
        queue = deque([(start, 0)])
        while queue:
            node, d = queue.popleft()
            if d >= depth:
                continue
            for neighbour in adj.get(node, []):
                if neighbour not in visited:
                    visited.add(neighbour)
                    result.append(neighbour)
                    queue.append((neighbour, d + 1))
        return result


# ── Constructors ─────────────────────────────────────────────────────────────

def build_lineage_graph(
    tables: list[dict],
    table_edges: list[dict],
    column_edges: list[dict],
) -> LineageGraph:
    """
    Build a LineageGraph from plain dicts.

    tables      — [{"full_name": str, "columns": [{name, type, nullable}], ...}]
    table_edges — [{"source": str, "target": str}]
    column_edges — [{"source_table", "source_column", "target_table", "target_column"}]
    """
    nodes: dict[str, TableNode] = {}
    for t in tables:
        full = t["full_name"]
        parts = full.split(".")
        cat = parts[0] if len(parts) >= 3 else ""
        sch = parts[1] if len(parts) >= 3 else (parts[0] if len(parts) == 2 else "")
        tbl = parts[-1]
        nodes[full] = TableNode(
            full_name=full,
            catalog=cat,
            schema_name=sch,
            table=tbl,
            columns=t.get("columns", []),
            role=t.get("role", "unknown"),
        )

    t_edges = [TableEdge(e["source"], e["target"]) for e in table_edges]
    c_edges = [
        ColumnEdge(
            source_table=e["source_table"],
            source_column=e["source_column"],
            target_table=e["target_table"],
            target_column=e["target_column"],
            transformation=e.get("transformation"),
        )
        for e in column_edges
    ]
    return LineageGraph(nodes, t_edges, c_edges)


def fetch_uc_lineage(
    table: str,
    workspace_url: str | None = None,
    token: str | None = None,
    depth: int = 2,
) -> dict:
    """
    Fetch table-level and column-level lineage from Unity Catalog REST API.

    Returns a dict compatible with build_lineage_graph().
    Inside a Databricks notebook, workspace_url and token are auto-detected
    from the notebook context when omitted — no PAT needed. Outside
    Databricks, pass both explicitly.
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests is required: pip install requests")

    if not workspace_url or not token:
        from dashui.context import databricks_context
        ctx = databricks_context()
        workspace_url = workspace_url or ctx.workspace_url
        token = token or ctx.api_token
    if not workspace_url or not token:
        raise ValueError(
            "workspace_url and token are required outside a Databricks notebook "
            "(no notebook context to auto-detect them from)."
        )

    headers = {"Authorization": f"Bearer {token}"}
    base = workspace_url.rstrip("/")

    visited_tables: set[str] = set()
    table_edges: list[dict] = []
    column_edges: list[dict] = []
    queue = deque([table])
    visited_tables.add(table)

    for _ in range(depth):
        next_queue: deque = deque()
        while queue:
            t = queue.popleft()
            resp = requests.get(
                f"{base}/api/2.0/lineage-tracking/table-lineages",
                headers=headers,
                params={"table_name": t},
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for up in data.get("upstream_tables", []):
                src = up.get("name", "")
                if src and src not in visited_tables:
                    visited_tables.add(src)
                    table_edges.append({"source": src, "target": t})
                    next_queue.append(src)
            for down in data.get("downstream_tables", []):
                tgt = down.get("name", "")
                if tgt and tgt not in visited_tables:
                    visited_tables.add(tgt)
                    table_edges.append({"source": t, "target": tgt})
                    next_queue.append(tgt)
        queue = next_queue

    # Column lineage for the root table
    col_resp = requests.get(
        f"{base}/api/2.0/lineage-tracking/column-lineages",
        headers=headers,
        params={"table_name": table},
        timeout=15,
    )
    if col_resp.status_code == 200:
        for col_data in col_resp.json().get("column_lineage", []):
            tgt_col = col_data.get("name", "")
            for up in col_data.get("upstream_columns", []):
                column_edges.append({
                    "source_table": up.get("table_name", ""),
                    "source_column": up.get("name", ""),
                    "target_table": table,
                    "target_column": tgt_col,
                })

    tables_list = [{"full_name": t, "columns": []} for t in visited_tables]
    return {
        "tables": tables_list,
        "table_edges": table_edges,
        "column_edges": column_edges,
    }
