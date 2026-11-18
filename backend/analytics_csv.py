"""Separate offline CSV CLI, without starting the FastAPI application."""

from app.analytics.csv_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
