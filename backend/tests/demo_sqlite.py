"""One-command demonstration using only a temporary, synthetic database."""

import csv
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.analytics.history_cli import main
from tests.sqlite_fixture import create_database


def run():
    example = Path(__file__).resolve().parents[2] / "docs/examples/synthetic-measurements.csv"
    with example.open(encoding="utf-8", newline="") as stream:
        rows = [(int(r["id"]), r["device_id"], r["metric"], float(r["value"]), r["unit"], r["ts"])
                for r in csv.DictReader(stream)]
    with TemporaryDirectory(prefix="iot-analytics-demo-") as directory:
        database = Path(directory) / "synthetic.db"
        output = Path(directory) / "report.json"
        create_database(database, rows)
        before = sha256(database.read_bytes()).hexdigest()
        for command in ("devices", "quality", "simulate"):
            args = [command, str(database), "--source-kind", "synthetic", "--output", str(output)]
            if command != "devices":
                args += ["--device-id", "SYNTHETIC_ROOM_A", "--from", "2026-01-05T08:00:00Z",
                         "--to", "2026-01-05T08:15:00Z"]
            if command == "simulate":
                args += ["--ttl-seconds", "180", "--strategy", "fixed", "--delay-minutes", "2",
                         "--on-watts", "60", "--off-watts", "2"]
            if main(args, source_type="sqlite") != 0:
                raise RuntimeError("Temporary database demonstration failed")
            document = json.loads(output.read_text(encoding="utf-8"))
            if command == "devices":
                print("SYNTHETIC devices:", [(d["device_id"], d["readings"]) for d in document["devices"]])
            elif command == "quality":
                q = document["quality"]
                print("Selected / unique / duplicates:", q["selected_rows"], q["accepted_unique_rows"], q["duplicate_rows"])
            else:
                energy = document["result"]["energy"]
                print("SIMULATED baseline / strategy [Wh]:", energy["baseline_wh"], energy["strategy_wh"])
        if sha256(database.read_bytes()).hexdigest() != before:
            raise AssertionError("Database changed")
        print("Database SHA-256 unchanged. Temporary files will be removed.")


if __name__ == "__main__":
    run()
