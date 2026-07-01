"""Smoke tests for dashgov package (no Spark, no UC required)."""


def test_import():
    import dashgov
    assert hasattr(dashgov, "__version__")


def test_launch_importable():
    from dashgov import launch
    assert callable(launch)


def test_public_api_importable():
    from dashgov import (
        LineageGraph, build_lineage_graph,
        parse_table_lineage, parse_column_lineage, parse_notebook_lineage,
        classify_table, classify_all,
    )
    assert callable(build_lineage_graph)
    assert callable(parse_table_lineage)
    assert callable(classify_table)
