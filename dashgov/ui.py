"""DashGov interactive UI for Databricks notebooks."""
from __future__ import annotations

_LIBRARY = "dashgov"


def env_setup() -> None:
    """Open the environment setup panel — where should dashgov read/write
    its configs? Defaults to the notebook's current working directory if
    never called."""
    try:
        import dashui
        from IPython.display import display
    except ImportError:
        raise RuntimeError("ipywidgets required. Run: %pip install ipywidgets") from None

    display(dashui.card([
        dashui.header("DashGov — Environment Setup", library=_LIBRARY),
        dashui.env_setup_panel(_LIBRARY).widget,
    ]))


def _role_colors() -> dict:
    from dashui import theme
    return {
        "entity": theme.INFO,
        "fact": theme.SUCCESS,
        "junction": theme.ACCENTS["dashsynthetic"],
        "aggregation": theme.WARNING,
        "staging": theme.MUTED_FOREGROUND,
        "unknown": theme.BORDER_STRONG,
    }


def _lineage_html(graph_dict: dict, focus_table: str = "") -> str:
    """Render a lineage graph as a simple HTML DAG (upstream → focus → downstream)."""
    from dashui import theme

    tables = graph_dict.get("tables", {})
    edges = graph_dict.get("table_edges", [])

    upstream = {e["source"] for e in edges if e["target"] == focus_table}
    downstream = {e["target"] for e in edges if e["source"] == focus_table}
    role_colors = _role_colors()

    def _box(name: str, pos: str) -> str:
        short = name.split(".")[-1]
        role = tables.get(name, {}).get("role", "unknown")
        color = role_colors.get(role, theme.BORDER_STRONG)
        border = f"2px solid {theme.PRIMARY}" if pos == "focus" else f"1px solid {theme.BORDER}"
        bg = theme.ACCENT_BG if pos == "focus" else theme.MUTED
        return (
            f"<div style='padding:8px 12px;border:{border};border-radius:{theme.RADIUS_MD};"
            f"background:{bg};color:{theme.FOREGROUND};font-size:12px;margin:4px;display:inline-block'>"
            f"<span style='color:{color};font-weight:600'>{short}</span>"
            f"<br/><span style='font-size:10px;color:{theme.MUTED_FOREGROUND}'>{role}</span></div>"
        )

    up_html = "".join(_box(t, "up") for t in sorted(upstream))
    focus_html = _box(focus_table, "focus") if focus_table else ""
    down_html = "".join(_box(t, "down") for t in sorted(downstream))
    arrow = f"<div style='font-size:20px;color:{theme.MUTED_FOREGROUND};margin:0 8px'>→</div>"

    return (
        f"<div style='display:flex;align-items:center;flex-wrap:wrap;gap:4px;"
        f"font-family:{theme.FONT_SANS};padding:12px;background:{theme.CARD};"
        f"border-radius:{theme.RADIUS_LG};border:1px solid {theme.BORDER}'>"
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

    saved = dashui.load_config(_LIBRARY, defaults={"dialect": "spark", "workspace_url": "", "table": "", "depth": 2, "scan_table": ""})
    ctx = dashui.databricks_context()

    # ── PII scan ──────────────────────────────────────────────────────────────
    scan_table = w.Text(description="Table:", placeholder="catalog.schema.table", value=saved["scan_table"])
    scan_sample = w.IntText(description="Sample rows:", value=1000, layout=w.Layout(width="200px"))
    scan_btn = dashui.action_button("Run PII Scan", style="success")
    tags_btn = dashui.action_button("Apply UC Tags", style="warning")
    scan_output = dashui.output_panel()
    scan_results = w.HTML(value="")
    _last_report: list = [None]

    def on_scan(b):
        with scan_output:
            scan_output.clear_output()
            scan_results.value = ""
            tbl = scan_table.value.strip()
            if not tbl:
                print("Warning: enter a table name")
                return
            _save_state()
            scan_btn.set_label("Scanning…")
            scan_btn.set_disabled(True)
            try:
                from dashgov.scanner import run_scan
                report = run_scan(table=tbl, sample_rows=scan_sample.value or 1000)
                _last_report[0] = report
                flagged = sum(1 for i in report.findings.values() if i["has_pii"] or i["sensitivity"] != "NONE")
                print(f"Scanned {len(report.findings)} columns — {flagged} flagged")
                scan_results.value = dashui.data_table_html(report.rows(), highlight_col="sensitivity")
            except Exception as e:
                print(f"Error: {e}")
            finally:
                scan_btn.set_label("Run PII Scan")
                scan_btn.set_disabled(False)

    def on_apply_tags(b):
        with scan_output:
            if _last_report[0] is None:
                print("Run a scan first")
                return
            try:
                _last_report[0].apply_tags()
            except Exception as e:
                print(f"Error: {e}")

    scan_btn.on_click(on_scan)
    tags_btn.on_click(on_apply_tags)

    # ── SQL parser ────────────────────────────────────────────────────────────
    sql_input = w.Textarea(
        description="SQL:",
        placeholder="Paste CREATE TABLE AS SELECT or INSERT INTO SELECT ...",
        layout=w.Layout(width="100%", height="120px"),
    )
    dialect_toggle = w.ToggleButtons(
        options=["spark", "snowflake", "bigquery", "trino"],
        description="Dialect:",
        value=saved["dialect"],
    )
    parse_btn = dashui.action_button("Parse Lineage from SQL", style="info")
    parse_output = dashui.output_panel()

    def on_parse(b):
        with parse_output:
            parse_output.clear_output()
            sql = sql_input.value.strip()
            if not sql:
                print("Warning: paste a SQL statement above")
                return
            _save_state()
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
                print(f"Error: {e}")

    parse_btn.on_click(on_parse)

    # ── UC live lineage ───────────────────────────────────────────────────────
    uc_workspace = w.Text(
        description="Workspace URL:",
        placeholder=ctx.workspace_url or "https://adb-xxx.azuredatabricks.net",
        value=saved["workspace_url"] or ctx.workspace_url,
    )
    uc_token = w.Password(
        description="Token:",
        placeholder="auto-detected from notebook" if ctx.api_token else "dapixxxxxxxx",
    )
    uc_table = w.Text(description="Table:", placeholder="catalog.schema.table", value=saved["table"])
    uc_depth = w.IntSlider(description="Depth:", value=saved["depth"], min=1, max=5)
    uc_btn = dashui.action_button("Fetch UC Lineage", style="success")
    uc_output = dashui.output_panel()
    lineage_viz = w.HTML(value="")

    def _save_state() -> None:
        try:
            dashui.save_config(_LIBRARY, {
                "dialect": dialect_toggle.value,
                "workspace_url": uc_workspace.value.strip(),
                "table": uc_table.value.strip(),
                "depth": uc_depth.value,
                "scan_table": scan_table.value.strip(),
            })
        except Exception:
            pass  # persistence is a convenience, never block the actual operation on it

    def on_uc_fetch(b):
        with uc_output:
            uc_output.clear_output()
            url = uc_workspace.value.strip()
            tok = uc_token.value.strip()
            tbl = uc_table.value.strip()
            if not tbl:
                print("Warning: fill in the table name")
                return
            if not ((url or ctx.workspace_url) and (tok or ctx.api_token)):
                print("Warning: no notebook context detected — fill in workspace URL and token")
                return
            _save_state()
            uc_btn.set_label("Fetching…")
            uc_btn.set_disabled(True)
            try:
                from dashgov.lineage import fetch_uc_lineage, build_lineage_graph
                raw = fetch_uc_lineage(tbl, url or None, tok or None, depth=uc_depth.value)
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
                print(f"Error: {e}")
            finally:
                uc_btn.set_label("Fetch UC Lineage")
                uc_btn.set_disabled(False)

    uc_btn.on_click(on_uc_fetch)

    env_accordion = w.Accordion(children=[dashui.env_setup_panel(_LIBRARY).widget])
    env_accordion.set_title(0, "Environment setup")
    env_accordion.selected_index = None

    context_note = (
        f"Workspace and token auto-detected from this notebook ({ctx.user or 'current user'}) "
        "— override below only if fetching from a different workspace."
        if ctx.is_databricks
        else "Requires a Databricks workspace URL and personal access token."
    )

    ui = dashui.card([
        dashui.header("DashGov — Data Lineage & Governance", library="dashgov"),
        env_accordion,

        dashui.section("Step 1: Scan a table for PII & sensitivity"),
        dashui.html(
            "<div style='font-size:12px;color:#666;margin-bottom:4px'>"
            "Samples the table, pattern-matches PII (with checksum validation for card "
            "numbers), classifies column sensitivity, and can write the result back as "
            "Unity Catalog column tags.</div>"
        ),
        w.HBox([scan_table, scan_sample]),
        w.HBox([scan_btn, tags_btn]),
        scan_output, scan_results,

        dashui.section("Step 2: Parse lineage from SQL"),
        dashui.html(
            "<div style='font-size:12px;color:#666;margin-bottom:4px'>"
            "Paste a CREATE TABLE AS SELECT or INSERT INTO SELECT to extract "
            "table and column lineage without a UC connection.</div>"
        ),
        sql_input, dialect_toggle, parse_btn, parse_output,

        dashui.section("Step 3: Fetch live lineage from Unity Catalog"),
        dashui.html(f"<div style='font-size:12px;color:#666;margin-bottom:4px'>{context_note}</div>"),
        w.HBox([uc_workspace, uc_token]),
        w.HBox([uc_table, uc_depth]),
        uc_btn, uc_output, lineage_viz,
    ])
    display(ui)
