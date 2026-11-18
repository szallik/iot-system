"""Primary offline history source: explicit SQLite path, read-only."""

from app.analytics.history_cli import main


if __name__ == "__main__":
    raise SystemExit(main(source_type="sqlite"))
