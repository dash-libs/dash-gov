"""Unit tests for GovernanceScanner (no Spark required)."""
import pytest


def test_import():
    import dashgov
    assert hasattr(dashgov, "__version__")


def test_launch_importable():
    from dashgov import launch
    assert callable(launch)


def test_main_class_importable():
    from dashgov import GovernanceScanner
    assert GovernanceScanner is not None
