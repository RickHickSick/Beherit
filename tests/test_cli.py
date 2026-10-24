"""Tests for Beherit Command-Line Interface (CLI)."""

import pytest
from beherit.cli import main


def test_cli_version(capsys):
    """Verify beherit -v displays version."""
    with pytest.raises(SystemExit) as exc:
        main.__wrapped__ if hasattr(main, "__wrapped__") else None
        import sys
        sys.argv = ["beherit", "-v"]
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Beherit" in captured.out or "Beherit" in captured.err


def test_cli_health(capsys):
    """Verify beherit health output."""
    import sys
    sys.argv = ["beherit", "health"]
    main()
    captured = capsys.readouterr()
    assert "Beherit Runtime" in captured.out
    assert "Agents Directory" in captured.out


def test_cli_list(capsys):
    """Verify beherit list output."""
    import sys
    sys.argv = ["beherit", "list"]
    main()
    captured = capsys.readouterr()
    assert "Beherit Agents Catalog" in captured.out
