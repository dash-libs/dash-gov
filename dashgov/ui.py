"""DashGov interactive UI for Databricks notebooks."""
from __future__ import annotations


def launch():
    try:
        import ipywidgets as w
        from IPython.display import display
    except ImportError:
        raise RuntimeError("ipywidgets required. Run: %pip install ipywidgets")

    table_input = w.Text(description="Table:", placeholder="catalog.schema.table")
    sample_slider = w.IntSlider(value=1000, min=100, max=10000, step=100,
                                description="Sample rows:")
    apply_tags_cb = w.Checkbox(value=False, description="Apply UC sensitivity tags after scan")

    run_btn = w.Button(description="🔍 Scan for PII & Governance Issues",
                       button_style="warning", layout=w.Layout(height="40px"))
    output = w.Output()

    def on_run(b):
        with output:
            output.clear_output()
            try:
                from dashgov.scanner import GovernanceScanner
                scanner = GovernanceScanner(table=table_input.value.strip())
                report = scanner.scan(sample_rows=sample_slider.value)
                report.display()
                if apply_tags_cb.value:
                    report.apply_tags()
            except Exception as e:
                print(f"❌ {e}")

    run_btn.on_click(on_run)

    ui = w.VBox([
        w.HTML("<h2 style='color:#B71C1C'>🛡️ DashGov — Data Governance</h2>"),
        w.HTML("<b>Target table</b>"), table_input,
        w.HTML("<b>Scan settings</b>"), sample_slider, apply_tags_cb,
        w.HTML("<hr>"), run_btn, output,
    ], layout=w.Layout(padding="16px", border="1px solid #ddd", border_radius="8px"))

    display(ui)
