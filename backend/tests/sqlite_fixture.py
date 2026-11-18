"""Synthetic SQLite fixture only. Production adapters never create databases."""

import sqlite3
from contextlib import closing


def create_database(path, rows=()):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE devices (
                id VARCHAR(64) PRIMARY KEY, nazwa VARCHAR(128) NOT NULL,
                typ VARCHAR(64) NOT NULL, online BOOLEAN NOT NULL,
                mqtt_topic VARCHAR(128), ieee_address VARCHAR(32), last_state TEXT, updated_at VARCHAR(32)
            );
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id VARCHAR(64) NOT NULL,
                metric VARCHAR(64) NOT NULL, value FLOAT NOT NULL,
                unit VARCHAR(16) NOT NULL, ts VARCHAR(32) NOT NULL,
                FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
            );
            CREATE INDEX ix_measurements_dev_metric_ts ON measurements(device_id,metric,ts);
            CREATE INDEX ix_measurements_ts ON measurements(ts);
        """)
        for device_id in sorted({row[1] for row in rows}):
            connection.execute("INSERT INTO devices(id,nazwa,typ,online) VALUES (?,?,?,?)",
                               (device_id, "SYNTHETIC", "occupancy_sensor", True))
        connection.executemany("INSERT INTO measurements(id,device_id,metric,value,unit,ts) VALUES (?,?,?,?,?,?)", rows)
