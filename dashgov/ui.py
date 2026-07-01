"""DashGov interactive UI for Databricks notebooks."""
from __future__ import annotations


def _lineage_html(graph_dict: dict, focus_table: str = "") -> str:
    """Render a lineage graph as a simple HTML DAG (upstream → focus → downstream)."""
    tables = graph_dict.get("tables", {})
    edges = graph_dict.get("table_edges", [])

    upstream = {e["source"] for e in edges if e["target"] == focus_table}
    downstream = {e["target"] for e in edges if e["source"] == focus_table}

    role_colors = {
        "entity": "#2563eb",
        "fact": "#16a34a",
        "junction": "#7c3aed",
        "aggregation": "#d97706",
        "staging": "#6b7280",
        "unknown": "#9ca3af",
    }

    def _box(name: str, pos: str) -> str:
        short = name.split(".")[-1]
        role = tables.get(name, {}).get("role", "unknown")
        color = role_colors.get(role, "#9ca3af")
        border = "3px solid #1d4ed8" if pos == "focus" else "1px solid #d1d5db"
        bg = "#eff6ff" if pos == "focus" else "#f9fafb"
        return (
            f"<div style='padding:8px 12px;border:{border};border-radius:6px;"
            f"background:{bg};color:#111;font-size:12px;margin:4px;display:inline-block'>"
            f"<span style='color:{color};font-weight:600'>{short}</span>"
            f"<br/><span style='font-size:10px;color:#6b7280'>{role}</span></div>"
        )

    up_html = "".join(_box(t, "up") for t in sorted(upstream))
    focus_html = _box(focus_table, "focus") if focus_table else ""
    down_html = "".join(_box(t, "down") for t in sorted(downstream))
    arrow = "<div style='font-size:20px;color:#9ca3af;margin:0 8px'>→</div>"

    return (
        "<div style='display:flex;align-items:center;flex-wrap:wrap;gap:4px;"
        "font-family:monospace;padding:12px;background:#fff;border-radius:8px;"
        "border:1px solid #e5e7eb'>"
        f"<div style='display:flex;flex-direction:column'>{up_html}</div>"
        f"{arrow if upstream else ''}"
        f"{focus_html}"
        f"{arrow if downstream else ''}"
        f"<div style='display:flex;flex-direction:column'>{down_html}</div>"
        "</div>"
    )


def launch():
    try:
        import ipywidgets as w
        from IPython.display import display
    except ImportError:
        raise RuntimeError("ipywidgets required. Run: %pip install ipywidgets")

    import dashui

    # ── SQL parser ────────────────────────────────────────────────────────────
    sql_input = w.Textarea(
        description="SQL:",
        placeholder="Paste CREATE TABLE AS SELECT or INSERT INTO SELECT ...",
        layout=w.Layout(width="100%", height="120px"),
    )
    dialect_toggle = w.ToggleButtons(
        options=["spark", "snowflake", "bigquery", "trino"],
        description="Dialect:",
        value="spark",
    )
    parse_btn = dashui.action_button("Parse Lineage from SQL", style="info", emoji="🔍")
    parse_output = dashui.output_panel()

    def on_parse(b):
        with parse_output:
            parse_output.clear_output()
            sql = sql_input.value.strip()
            if not sql:
                print("⚠️  Paste a SQL statement above")
                return
            try:
                from dashgov.parser import parse_table_lineage, parse_column_lineage
                tl = parse_table_lineage(sql, dialect=dialect_toggle.value)
                print(f"Type    : {tl['type']}")
                print(f"Target  : {tl['target'] or '—'}")
                print(f"Sources : {', '.join(tl['sources']) or '—'}")
                if tl["target"]:
                    cl = parse_column_lineage(sql, tl["target"], dialect=dialect_toggle.value)
                    if cl:
                        print("\nColumn lineage:")
                        for c in cl:
                            src = (
                                f"{c['source_table']}.{c['source_column']}"
                                if c["source_table"] else c.get("expression", "?")
                            )
                            print(f"  {src:40s} → {c['target_column']}")
            except Exception as e:
                print(f"❌ {e}")

    parse_btn.on_click(on_parse)

    # ── UC live lineage ───────────────────────────────────────────────────────
    uc_workspace = w.Text(
        description="Workspace URL:",
        placeholder="https://adb-xxx.azuredatabricks.net",
    )
    uc_token = w.Password(description="Token:", placeholder="dapixxxxxxxx")
    uc_table = w.Text(description="Table:", placeholder="catalog.schema.table")
    uc_depth = w.IntSlider(description="Depth:", value=2, min=1, max=5)
    uc_btn = dashui.action_button("Fetch UC Lineage", style="success", emoji="🌐")
    uc_output = dashui.output_panel()
    lineage_viz = w.HTML(value="")

    def on_uc_fetch(b):
        with uc_output:
            uc_output.clear_output()
            url = uc_workspace.value.strip()
            tok = uc_token.value.strip()
            tbl = uc_table.value.strip()
            if not (url and tok and tbl):
                print("⚠️  Fill in workspace URL, token, and table name")
                return
            try:
                from dashgov.lineage import fetch_uc_lineage, build_lineage_graph
                raw = fetch_uc_lineage(tbl, url, tok, depth=uc_depth.value)
                graph = build_lineage_graph(
                    raw["tables"], raw["table_edges"], raw["column_edges"]
                )
                s = graph.summary()
                print(f"Tables : {s['total_tables']}")
                print(f"Edges  : {s['total_table_edges']} table, {s['total_column_edges']} column")
                print(f"Roots  : {', '.join(s['root_sources']) or '—'}")
                print(f"Sinks  : {', '.join(s['leaf_sinks']) or '—'}")
                lineage_viz.value = _lineage_html(graph.to_dict(), focus_table=tbl)
                imp = graph.impact_analysis(tbl)
                if imp["all_downstream"]:
                    print(f"\nImpact if {tbl} changes:")
                    for t in imp["all_downstream"]:
                        print(f"  ↓ {t}")
            except Exception as e:
                print(f"❌ {e}")

    uc_btn.on_click(on_uc_fetch)

    ui = dashui.card([
        dashui.header("DashGov — Data Lineage & Governance", library="dashgov", emoji="🔗"),

        dashui.section("Step 1: Parse lineage from SQL"),
        dashui.html(
            "<div style='font-size:12px;color:#666;margin-bottom:4px'>"
            "Paste a CREATE TABLE AS SELECT or INSERT INTO SELECT to extract "
            "table and column lineage without a UC connection.</div>"
        ),
        sql_input, dialect_toggle, parse_btn, parse_output,

        dashui.section("Step 2: Fetch live lineage from Unity Catalog"),
        dashui.html(
            "<div style='font-size:12px;color:#666;margin-bottom:4px'>"
            "Requires a Databricks workspace URL and personal access token.</div>"
        ),
        w.HBox([uc_workspace, uc_token]),
        w.HBox([uc_table, uc_depth]),
        uc_btn, uc_output, lineage_viz,
    ])
    display(ui)
