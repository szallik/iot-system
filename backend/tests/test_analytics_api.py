"""Isolated ASGI requests: no main, production lifespan, MQTT or workstation DB."""
import asyncio
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

with patch.dict(os.environ, {"DEBUG": "false", "JWT_SECRET_KEY": "analytics-test-secret",
                             "DATABASE_URL": "sqlite:///never-open-production.db"}):
    from app.routers.analytics import router, get_service
    from app.core.auth_tokens import create_access_token
    from app.core.exceptions import (AppError, app_error_handler, http_exception_handler,
                                     validation_exception_handler)

from app.analytics.api_service import AnalyticsService
from app.analytics.sqlite_history import import_sqlite
from app.analytics.history_service import jsonable, simulate_import
from app.schemas.analytics import CompareRequest, SimulationResult
from tests.sqlite_fixture import create_database


async def asgi_request(app, method, path, body=None, token=None, query=None):
    """Exercise the real ASGI routing, validation, dependencies and response serialization."""
    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", ("Bearer " + token).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": urlencode(query or {}).encode(), "headers": headers,
             "client": ("test", 1234), "server": ("test", 80), "root_path": ""}
    messages = []
    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"", "more_body": False}
    async def send(message):
        messages.append(message)
    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    data = json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
    return status, data


class AnalyticsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.db"
        rows = [(n+1, "sensor", "occupancy", value, "bool", stamp) for n, (stamp, value) in enumerate([
            ("2026-01-01T07:59:00Z", 1), ("2026-01-01T08:00:00Z", 0),
            ("2026-01-01T08:30:00Z", 1), ("2026-01-01T09:00:00Z", 0),
            ("2026-01-01T10:00:00Z", 1)])]
        rows.append((10, "other", "occupancy", 0, "bool", "2026-01-01T08:00:00Z"))
        create_database(self.path, rows)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""CREATE TABLE users (
                id INTEGER PRIMARY KEY, login VARCHAR(64) NOT NULL,
                hash_pin VARCHAR(128) NOT NULL, password_hash VARCHAR(128),
                rola VARCHAR(32) NOT NULL, aktywny BOOLEAN NOT NULL, created_at VARCHAR(32) NOT NULL);
                INSERT INTO users VALUES (1,'reader','unused',NULL,'user',1,'2026-01-01T00:00:00Z');
                INSERT INTO users VALUES (2,'blocked','unused',NULL,'guest_no_access',1,'2026-01-01T00:00:00Z');
                INSERT INTO users VALUES (3,'disabled','unused',NULL,'admin',0,'2026-01-01T00:00:00Z');
                INSERT INTO users VALUES (4,'admin','unused',NULL,'admin',1,'2026-01-01T00:00:00Z');""")
        self.service = AnalyticsService(str(self.path), source_kind="synthetic")
        self.app = FastAPI()  # Deliberately never import app.main or its lifespan.
        self.app.add_exception_handler(AppError, app_error_handler)
        self.app.add_exception_handler(HTTPException, http_exception_handler)
        self.app.add_exception_handler(RequestValidationError, validation_exception_handler)
        self.app.include_router(router)
        self.app.dependency_overrides[get_service] = lambda: self.service
        self.token = create_access_token({"sub": "1"})
        self.period = {"from": "2026-01-01T08:00:00Z", "to": "2026-01-01T09:00:00Z"}
        self.constraints = {"max_short_returns": 10, "max_shutdowns": 10,
                            "min_known_share": .5, "max_uncertain_returns": 10}
        self.body = {"device_id": "sensor", "period": self.period, "ttl_seconds": 3600,
                     "receiver": {"on_watts": 60, "off_watts": 2},
                     "strategies": [{"kind": "fixed", "delay_seconds": 60}], "constraints": self.constraints}

    def call(self, endpoint, body=None, token="default", query=None):
        return asyncio.run(asgi_request(self.app, "POST" if body is not None else "GET",
                           "/api/analytics/" + endpoint, body, self.token if token == "default" else token, query))

    def recommend_body(self):
        body = deepcopy(self.body)
        body["selection_period"] = body.pop("period")
        body["selection_constraints"] = body.pop("constraints")
        body["evaluation_period"] = {"from": "2026-01-01T09:00:00Z", "to": "2026-01-01T10:00:00Z"}
        body["evaluation_constraints"] = dict(self.constraints, max_shutdowns=0)
        body["strategies"].append({"kind": "fixed", "delay_seconds": 600})
        return body

    def test_devices_and_quality_without_ttl(self):
        status, data = self.call("devices")
        self.assertEqual(status, 200)
        self.assertEqual([d["device_id"] for d in data["devices"]], ["other", "sensor"])
        status, data = self.call("quality", query=dict(self.period, device_id="sensor"))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["quality"]["selected_rows"], 3)  # includes predecessor, excludes 09:00
        self.assertNotIn(str(self.path), json.dumps(data))

    def test_compare_matches_core_and_no_database_writes_or_mqtt(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        status, data = self.call("compare", self.body)
        self.assertEqual(status, 200, data)
        request = CompareRequest.model_validate(self.body)
        imported = import_sqlite(self.path, device_id="sensor", metric="occupancy",
                                 start=request.period.start, end=request.period.end)
        expected = jsonable(simulate_import(imported, start=request.period.start, end=request.period.end,
            ttl=timedelta(hours=1), strategy=request.strategies[0].domain(), model=request.receiver.domain(),
            short_return_window=timedelta(minutes=2)))
        row = data["comparison"]["candidates"][0]
        self.assertEqual(row["simulation"], SimulationResult.model_validate(expected).model_dump(mode="json"))
        self.assertAlmostEqual(row["known_energy_wh"], 60*31/60 + 2*29/60)
        self.assertTrue(data["assumptions"]["energy_simulated"])
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        for module in ("app.main", "app.core.database", "app.services.mqtt_service", "paho.mqtt.client"):
            self.assertNotIn(module, sys.modules)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["synthetic.db"])

    def test_authorization_all_endpoints(self):
        for endpoint, body in (("devices", None), ("quality", None), ("compare", self.body), ("recommend", self.recommend_body())):
            for token, expected in ((None, 401), ("bad-token", 401),
                    (create_access_token({"sub": "999"}), 401),
                    (create_access_token({"sub": "2"}), 403), (create_access_token({"sub": "3"}), 403)):
                with self.subTest(endpoint=endpoint, status=expected):
                    status, data = self.call(endpoint, body, token=token, query=dict(self.period, device_id="sensor") if endpoint == "quality" else None)
                    self.assertEqual(status, expected, data)
                    self.assertIn("error", data)
        self.assertEqual(self.call("devices", token=create_access_token({"sub": "4"}))[0], 200)

    def test_validation(self):
        for missing in ("ttl_seconds", "receiver"):
            body = deepcopy(self.body); del body[missing]
            self.assertEqual(self.call("compare", body)[0], 422)
        for field, value in (("ttl_seconds", 0), ("ttl_seconds", True), ("ttl_seconds", float("inf")),
                             ("ttl_seconds", 0.00000001), ("database_path", "any.db"), ("device_id", " ")):
            with self.subTest(field=field, value=value):
                body = deepcopy(self.body); body[field] = value
                self.assertEqual(self.call("compare", body)[0], 422)
        for changes in ({"on_watts": -1}, {"on_watts": float("nan")}, {"off_watts": True}):
            body = deepcopy(self.body); body["receiver"].update(changes)
            self.assertEqual(self.call("compare", body)[0], 422)
        for period in ({"from": "2026-01-01T08:00:00", "to": self.period["to"]},
                       {"from": self.period["to"], "to": self.period["from"]},
                       {"from": 1234, "to": self.period["to"]}):
            body = deepcopy(self.body); body["period"] = period
            self.assertEqual(self.call("compare", body)[0], 422)
        for change in ({"min_known_share": 2}, {"max_shutdowns": -1}, {"max_shutdowns": True}):
            body = deepcopy(self.body); body["constraints"].update(change)
            self.assertEqual(self.call("compare", body)[0], 422)
        self.assertEqual(self.call("devices", query={"database_path": "x"})[0], 422)
        self.assertEqual(self.call("quality", query=dict(self.period, device_id="sensor", ttl_seconds=1))[0], 422)

    def test_schedule_and_frozen_adaptive(self):
        body = deepcopy(self.body)
        schedule = {"kind": "schedule", "default_delay_seconds": 60, "windows": []}
        adaptive = {"kind": "adaptive", "groups": [{"name": "all", "weekdays": list(range(7)),
                       "hours": [["00:00", "24:00"]], "delay_seconds": 60}]}
        body["strategies"] += [schedule, adaptive]
        status, data = self.call("compare", body)
        self.assertEqual(status, 200, data)
        self.assertEqual(len({c["known_energy_wh"] for c in data["comparison"]["candidates"]}), 1)
        schedule["windows"] = [{"weekdays": [0], "start": "08:00", "end": "16:00", "delay_seconds": 60}]*2
        self.assertEqual(self.call("compare", body)[0], 422)
        schedule["windows"] = []; schedule["timezone"] = "not/a/zone"
        self.assertEqual(self.call("compare", body)[0], 422)
        body["strategies"] = [dict(adaptive, strategy_config="secret.json")]
        self.assertEqual(self.call("compare", body)[0], 422)

    def test_no_data_is_unknown_and_no_recommendation(self):
        body = deepcopy(self.body); body["device_id"] = "missing"
        status, data = self.call("compare", body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["comparison"]["unknown_seconds"], 3600)
        row = data["comparison"]["candidates"][0]
        self.assertEqual(row["shutdowns"], 0)
        self.assertFalse(row["admissible"])
        body = self.recommend_body(); body["device_id"] = "missing"
        status, data = self.call("recommend", body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["status"], "no_recommendation")
        self.assertIsNone(data["evaluation"])
        self.assertTrue(data["reasons"])

    def test_conflicts_and_invalid_dates_reported_and_block_simulation(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO measurements VALUES (20,'sensor','occupancy',1,'bool','2026-01-01T08:00:00Z')")
            db.execute("INSERT INTO measurements VALUES (21,'sensor','occupancy',1,'bool','invalid')")
        status, quality = self.call("quality", query=dict(self.period, device_id="sensor"))
        self.assertEqual(status, 200)
        self.assertEqual(quality["quality"]["conflicting_timestamps"], 1)
        self.assertEqual(quality["quality"]["invalid_timestamps"], 1)
        for endpoint, body in (("compare", self.body), ("recommend", self.recommend_body())):
            status, data = self.call(endpoint, body)
            self.assertEqual(status, 409, data)
            self.assertIn("Symulacja zablokowana", data["error"]["message"])

    def test_limits(self):
        self.service = replace(self.service, max_records=2)
        for endpoint, body in (("devices", None), ("quality", None), ("compare", self.body), ("recommend", self.recommend_body())):
            status, data = self.call(endpoint, body, query=dict(self.period, device_id="sensor") if endpoint == "quality" else None)
            self.assertEqual(status, 413, data)
            self.assertEqual(data["error"]["code"], "ANALYTICS_LIMIT_EXCEEDED")
        self.service = replace(self.service, max_records=20000, max_configurations=1)
        self.assertEqual(self.call("recommend", self.recommend_body())[0], 413)
        body = deepcopy(self.body); body["period"]["to"] = "2027-01-01T00:00:00Z"
        self.assertEqual(self.call("compare", body)[0], 413)

    def test_selection_frozen_and_holdout_constraints_reported(self):
        body = self.recommend_body()
        status, first = self.call("recommend", body)
        self.assertEqual(status, 200, first)
        self.assertEqual(first["selected_identifier"], first["evaluation"]["candidates"][0]["identifier"])
        self.assertFalse(first["evaluation"]["candidates"][0]["admissible"])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE measurements SET value=1 WHERE id=4")
        status, second = self.call("recommend", body)
        self.assertEqual(status, 200, second)
        self.assertEqual(first["selection"], second["selection"])
        self.assertEqual(first["selected_identifier"], second["selected_identifier"])
        self.assertTrue(second["evaluation"]["candidates"][0]["admissible"])
        body["evaluation_period"] = self.period
        self.assertEqual(self.call("recommend", body)[0], 422)

    def test_unknown_and_uncertain_returns(self):
        body = deepcopy(self.body); body["ttl_seconds"] = 600
        body["strategies"].append({"kind": "fixed", "delay_seconds": 600})
        status, data = self.call("compare", body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["comparison"]["unknown_seconds"], 2400)
        self.assertEqual(data["comparison"]["candidates"][0]["uncertain_returns"], 1)
        self.assertEqual(data["comparison"]["candidates"][0]["short_returns"], 0)
        body["constraints"]["max_uncertain_returns"] = 0
        _, data = self.call("compare", body)
        row = data["comparison"]["candidates"][0]
        self.assertFalse(row["admissible"])
        self.assertTrue(any("Niepewne powroty" in reason for reason in row["rejection_reasons"]))

    def test_configured_source_and_readonly_connections(self):
        from app.core.config import settings
        self.app.dependency_overrides.clear()
        connect = sqlite3.connect
        connections = []
        def checked(database, *args, **kwargs):
            self.assertTrue(database.endswith("?mode=ro"))
            self.assertTrue(kwargs.get("uri"))
            connections.append(database)
            return connect(database, *args, **kwargs)
        with patch.object(settings, "DATABASE_URL", "sqlite:///" + str(self.path)), patch("sqlite3.connect", checked):
            status, data = self.call("compare", self.body)
        self.assertEqual(status, 200, data)
        self.assertEqual(len(connections), 2)  # account and history, both read-only
        with patch.object(settings, "DATABASE_URL", "postgresql://localhost/test"):
            self.assertEqual(self.call("devices")[0], 503)

    def test_expired_token_and_shared_permission_rule(self):
        from app.core.config import settings
        from app.core.auth_tokens import account_has_access
        with patch.object(settings, "JWT_EXPIRE_MINUTES", -1):
            expired = create_access_token({"sub": "1"})
        self.assertEqual(self.call("devices", token=expired)[0], 401)
        for active in (True, False):
            for role in ("admin", "user", "blocked", "guest_no_access", "none", "other"):
                self.assertEqual(account_has_access(active, role),
                                 active and role not in ("blocked", "guest_no_access", "none"))

    def test_duplicate_quality_and_adaptive_frozen_in_recommendation(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO measurements VALUES (20,'sensor','occupancy',0,'bool','2026-01-01T09:00:00+01:00')")
        status, quality = self.call("quality", query=dict(self.period, device_id="sensor"))
        self.assertEqual(status, 200)
        self.assertEqual(quality["quality"]["duplicate_rows"], 1)
        body = self.recommend_body()
        body["strategies"] = [{"kind": "adaptive", "groups": [{"name": "all", "weekdays": list(range(7)),
                               "hours": [["00:00", "24:00"]], "delay_seconds": 60}]}]
        original = deepcopy(body)
        status, data = self.call("recommend", body)
        self.assertEqual(status, 200, data)
        self.assertEqual(body, original)
        self.assertEqual(data["selection"]["candidates"][0]["configuration"],
                         data["evaluation"]["candidates"][0]["configuration"])

    def test_missing_schema(self):
        empty = self.path.parent / "empty.db"
        with closing(sqlite3.connect(empty)):
            pass
        self.service = replace(self.service, database_path=str(empty))
        self.assertEqual(self.call("devices")[0], 503)

    def test_threadpool_and_openapi(self):
        main_thread = threading.get_ident()
        original = AnalyticsService.compare
        seen = []
        def checked(service, request):
            seen.append(threading.get_ident())
            return original(service, request)
        with patch.object(AnalyticsService, "compare", checked):
            self.assertEqual(self.call("compare", self.body)[0], 200)
        self.assertTrue(seen and all(t != main_thread for t in seen))
        schema = self.app.openapi()
        self.assertEqual(len(schema["paths"]), 7)
        self.assertIn("CompareResponse", schema["components"]["schemas"])

    def test_missing_database_never_created_and_server_path_not_disclosed(self):
        self.service = replace(self.service, database_path=str(self.path.parent / "missing.db"))
        status, data = self.call("devices")
        self.assertEqual(status, 503, data)
        self.assertFalse(Path(self.service.database_path).exists())
        self.assertNotIn(self.service.database_path, json.dumps(data))


if __name__ == "__main__":
    unittest.main()
