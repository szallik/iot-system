"""Backward-compatible entry point for the shared offline history CLI."""

from .history_cli import build_parser, jsonable, load_strategy, main, seconds, simulate_import


if __name__ == "__main__":
    raise SystemExit(main())
