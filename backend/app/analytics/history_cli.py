"""Shared offline CSV/SQLite CLI, validation, simulation and JSON serialization."""

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from math import isfinite
from pathlib import Path
import sys

from .contexts import AdaptiveDelay, ContextGroup, ContextPartition
from .csv_history import import_measurements, import_sensor_period, parse_timestamp
from .sqlite_history import import_sqlite, list_devices
from .energy import ReceiverModel
from .strategies import FixedDelay, ScheduleWindow, WeeklySchedule
from .history_service import jsonable, seconds, simulate_import


from .environment import analyze_environment
from .electricity import analyze_electricity
from .heat import HEAT_METRICS, summarize_heat


SENSOR_COMMANDS = ("environment", "electricity", "heat")


def load_strategy(args):
    if args.strategy == "fixed":
        if args.delay_minutes is None or args.strategy_config is not None:
            raise ValueError("fixed wymaga --delay-minutes i nie przyjmuje --strategy-config")
        return FixedDelay(seconds(args.delay_minutes * 60))
    if args.delay_minutes is not None or args.strategy_config is None:
        raise ValueError("schedule/adaptive wymaga --strategy-config, bez --delay-minutes")
    document = json.loads(Path(args.strategy_config).read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Konfiguracja strategii musi byc obiektem JSON")
    if args.strategy == "schedule":
        return WeeklySchedule(seconds(document["default_delay_seconds"]), tuple(
            ScheduleWindow(tuple(w["weekdays"]), w["start"], w["end"], seconds(w["delay_seconds"]))
            for w in document["windows"]), document["timezone"])
    # Load only the frozen policy from the earlier training artifact; never fit to CSV.
    if document.get("schema_version") != 1 or document.get("status") != "recommended":
        raise ValueError("Artefakt adaptacji musi miec schema_version=1 i status=recommended")
    config = document["strategy"]
    groups = tuple(ContextGroup(g["name"], tuple(g["weekdays"]), tuple(tuple(pair) for pair in g["hours"]))
                   for g in config["groups"])
    return AdaptiveDelay(ContextPartition(groups, config["timezone"]),
                         tuple(seconds(g["delay_seconds"]) for g in config["groups"]))


def sensor_result(args, start, end, document):
    environment = args.command == "environment"
    metrics = {"environment": (args.metric,), "electricity": ("energy", "power"), "heat": HEAT_METRICS}[args.command]
    histories = import_sensor_period(args.source_path, device_id=args.device_id, metrics=metrics,
                                     start=start, end=end, command=args.command)
    document["source"].update(histories[metrics[0]].source)
    document["quality"] = {metric: history.quality for metric, history in histories.items()}
    if not environment:
        document.update(device_type=args.device_type, device_type_source="user_declared")
    for metric, history in histories.items():
        if history.quality["blocking_reasons"]:
            raise ValueError(metric + ": " + "; ".join(history.quality["blocking_reasons"]))
    if args.command == "electricity":
        return analyze_electricity(**histories, start=start, end=end, price_per_kwh=args.price_per_kwh)
    ttl = seconds(args.ttl_seconds)
    document["ttl_seconds"] = args.ttl_seconds
    if environment:
        document.update(lower_threshold=args.lower_threshold, upper_threshold=args.upper_threshold)
        return analyze_environment(histories[args.metric], start=start, end=end, ttl=ttl,
                                   lower_threshold=args.lower_threshold, upper_threshold=args.upper_threshold)
    return summarize_heat(histories, start=start, end=end, ttl=ttl)


def build_parser(source_type="csv"):
    parser = argparse.ArgumentParser(description=f"Offline measurements {source_type}: jakosc i symulacja")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (("devices", "quality", "simulate") if source_type == "sqlite" else ("quality", "simulate", *SENSOR_COMMANDS)):
        child = subparsers.add_parser(command)
        child.add_argument("source_path")
        if command == "devices":
            child.set_defaults(device_id=None, metric="occupancy")
        else:
            child.add_argument("--device-id", required=True)
            if command == "environment":
                child.add_argument("--metric", required=True, choices=("temperature", "humidity"))
                child.add_argument("--lower-threshold", type=float)
                child.add_argument("--upper-threshold", type=float)
            elif command in ("heat", "electricity"):
                child.set_defaults(metric=None)
                device_type = "heat_meter" if command == "heat" else "power_strip"
                child.add_argument("--device-type", choices=(device_type,), required=True)
            else:
                child.add_argument("--metric", default="occupancy")
            if command == "electricity":
                child.add_argument("--price-per-kwh", type=float)
            if command in ("environment", "heat"):
                child.add_argument("--ttl-seconds", type=float, required=True)
        source_kinds = ("synthetic", "real", "unknown")
        default_kind = "unknown"
        if source_type == "csv" and command in ("quality", "simulate"):
            source_kinds += ("hardware", "unspecified")
            default_kind = "unspecified"
        child.add_argument("--source-kind", choices=source_kinds, default=default_kind)
        child.add_argument("--output", required=True)
        if command in (*SENSOR_COMMANDS, "simulate") or (source_type == "sqlite" and command == "quality"):
            child.add_argument("--from", dest="start", required=True)
            child.add_argument("--to", dest="end", required=True)
        if command == "simulate":
            child.add_argument("--ttl-seconds", type=float, required=True)
            child.add_argument("--strategy", choices=("fixed", "schedule", "adaptive"), required=True)
            child.add_argument("--delay-minutes", type=float)
            child.add_argument("--strategy-config")
            child.add_argument("--on-watts", type=float, required=True)
            child.add_argument("--off-watts", type=float, required=True)
            child.add_argument("--short-return-seconds", type=float, default=120)
    return parser


def main(argv=None, *, source_type="csv") -> int:
    args = build_parser(source_type).parse_args(argv)
    output = Path(args.output)
    sources = [Path(args.source_path)]
    if source_type == "sqlite":
        sources.extend(Path(str(Path(args.source_path).resolve()) + suffix) for suffix in ("-wal", "-shm", "-journal"))
    if getattr(args, "strategy_config", None):
        sources.append(Path(args.strategy_config))
    if any(output.resolve() == path.resolve() for path in sources):
        print("Plik wyjsciowy nie moze nadpisac zrodla, plikow SQLite ani konfiguracji strategii", file=sys.stderr)
        return 2
    document = {"schema_version": 1, "command": args.command, "status": "ok",
                "source": {"path": str(Path(args.source_path).resolve()), "kind": args.source_kind},
                "device_id": args.device_id, "metric": args.metric, "range": None,
                "ttl_seconds": None, "strategy": None, "receiver_model_watts": None, "quality": None}
    code = 0
    try:
        if hasattr(args, "start"):
            start, end = parse_timestamp(args.start), parse_timestamp(args.end)
            if start >= end:
                raise ValueError("Zakres musi spelniac from < to (koniec wylaczny)")
            document["range"] = {"start": start.isoformat(), "end": end.isoformat(), "end_exclusive": True}
        if args.command in SENSOR_COMMANDS:
            document["result"] = sensor_result(args, start, end, document)
        elif args.command == "devices":
            result = list_devices(args.source_path)
            document["source"].update(result["source"])
            document["devices"] = result["devices"]
        else:
            imported = (import_sqlite(args.source_path, device_id=args.device_id, metric=args.metric, start=start, end=end)
                        if source_type == "sqlite" else
                        import_measurements(args.source_path, device_id=args.device_id, metric=args.metric))
            document["source"].update(imported.source)
            document["quality"] = imported.quality
            if args.command == "quality" and source_type == "csv":
                document["range"] = imported.quality["time_range"]
        if args.command == "simulate":
            ttl = seconds(args.ttl_seconds)
            model = ReceiverModel(args.on_watts, args.off_watts)
            policy = load_strategy(args)
            if not isfinite(args.short_return_seconds) or args.short_return_seconds < 0:
                raise ValueError("Prog krotkiego powrotu musi byc skonczony i nieujemny")
            document.update(range={"start": start.isoformat(), "end": end.isoformat(), "end_exclusive": True},
                            ttl_seconds=ttl.total_seconds(),
                            strategy={"name": policy.name, "configuration": policy.configuration},
                            receiver_model_watts=asdict(model), short_return_seconds=args.short_return_seconds)
            document["result"] = simulate_import(imported, start=start, end=end, ttl=ttl, strategy=policy,
                                                 model=model, short_return_window=timedelta(seconds=args.short_return_seconds))
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
        document.update(status="blocked", error=str(error))
        code = 2
    try:
        output.write_text(json.dumps(jsonable(document), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    except (OSError, ValueError) as error:
        print(f"Nie zapisano JSON: {error}", file=sys.stderr)
        return 2
    print(f"{document['status']}: {output}")
    if code:
        print(document["error"], file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
