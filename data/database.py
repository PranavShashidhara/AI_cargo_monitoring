"""
Storage Layer — SQLite + File System
=====================================
Handles all persistent storage for the cargo monitoring system.

Tables:
  - shipments         : static shipment metadata
  - telemetry         : raw sensor readings (append-only)
  - anomaly_events    : flagged anomalies from detection agents
  - risk_assessments  : spoilage probability + action window from RAG
  - agent_decisions   : orchestrator decisions + human approvals
  - audit_logs        : compliance trail for FDA/GDP

Usage:
    from data.database import CargoDatabase

    db = CargoDatabase()               # creates cargo_monitor.db
    db.init_tables()                    # create schema
    db.insert_shipment({...})           # load metadata
    db.insert_telemetry_reading({...})  # store each reading
    db.insert_anomaly_event({...})      # detection agent writes here
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(__file__).parent / "cargo_monitor.db"


class CargoDatabase:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def init_tables(self):
        """Create all tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS shipments (
                shipment_id     TEXT PRIMARY KEY,
                cargo_type      TEXT NOT NULL,
                vaccine_type    TEXT NOT NULL,
                temp_range_min  REAL NOT NULL,
                temp_range_max  REAL NOT NULL,
                viability_window_hrs REAL NOT NULL,
                origin_airport  TEXT,
                transit_hub     TEXT,
                dest_airport    TEXT,
                destination_hospital TEXT,
                carrier         TEXT,
                insurance_days  INTEGER,
                pack_time       TEXT NOT NULL,
                status          TEXT DEFAULT 'in_transit',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS telemetry (
                reading_id      TEXT PRIMARY KEY,
                shipment_id     TEXT NOT NULL REFERENCES shipments(shipment_id),
                timestamp       TEXT NOT NULL,
                elapsed_hrs     REAL,
                leg             TEXT,
                leg_mode        TEXT,
                lat             REAL,
                lng             REAL,
                speed_kmh       REAL,
                temp_c          REAL,
                humidity_pct    REAL,
                shock_g         REAL,
                door_open       INTEGER DEFAULT 0,
                battery_pct     REAL,
                flight_status   TEXT,
                customs_status  TEXT,
                eta_delta_hrs   REAL,
                ambient_temp_c  REAL,
                route_alerts    TEXT,
                airport_conditions TEXT,
                carrier_id      TEXT,
                recorded_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS anomaly_events (
                event_id        TEXT PRIMARY KEY,
                shipment_id     TEXT NOT NULL REFERENCES shipments(shipment_id),
                reading_id      TEXT REFERENCES telemetry(reading_id),
                anomaly_type    TEXT NOT NULL,
                severity        TEXT NOT NULL,
                details         TEXT,
                detected_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS risk_assessments (
                assessment_id   TEXT PRIMARY KEY,
                anomaly_event_id TEXT REFERENCES anomaly_events(event_id),
                shipment_id     TEXT NOT NULL REFERENCES shipments(shipment_id),
                spoilage_prob   REAL,
                action_window_hrs REAL,
                delay_budget_hrs REAL,
                shipment_viable INTEGER DEFAULT 1,
                rag_citation    TEXT,
                risk_narrative  TEXT,
                recommended_action TEXT,
                assessed_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_decisions (
                decision_id     TEXT PRIMARY KEY,
                risk_assessment_id TEXT REFERENCES risk_assessments(assessment_id),
                shipment_id     TEXT NOT NULL REFERENCES shipments(shipment_id),
                cascade_triggered INTEGER DEFAULT 0,
                action_plan     TEXT,
                decision        TEXT,
                approved_by     TEXT,
                approved_at     TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                log_id          TEXT PRIMARY KEY,
                decision_id     TEXT REFERENCES agent_decisions(decision_id),
                shipment_id     TEXT NOT NULL REFERENCES shipments(shipment_id),
                action_type     TEXT NOT NULL,
                outcome         TEXT,
                state_snapshot  TEXT,
                compliance_flags TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_shipment
                ON telemetry(shipment_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_anomaly_shipment
                ON anomaly_events(shipment_id, detected_at);

            CREATE INDEX IF NOT EXISTS idx_audit_shipment
                ON audit_logs(shipment_id, created_at);
        """)
        self.conn.commit()

    # ── Shipments ──

    def insert_shipment(self, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO shipments
            (shipment_id, cargo_type, vaccine_type, temp_range_min, temp_range_max,
             viability_window_hrs, origin_airport, transit_hub, dest_airport,
             destination_hospital, carrier, insurance_days, pack_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["shipment_id"], data.get("cargo_type", "pharmaceutical_vaccine"),
            data["vaccine_type"], data["temp_range_min"], data["temp_range_max"],
            data["viability_window_hrs"], data.get("origin_airport"),
            data.get("transit_hub"), data.get("dest_airport"),
            data.get("destination_hospital"), data.get("carrier"),
            data.get("insurance_days", 4), data["pack_time"],
            data.get("status", "in_transit"),
        ))
        self.conn.commit()

    def get_shipment(self, shipment_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM shipments WHERE shipment_id = ?", (shipment_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Telemetry ──

    def insert_telemetry_reading(self, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO telemetry
            (reading_id, shipment_id, timestamp, elapsed_hrs, leg, leg_mode,
             lat, lng, speed_kmh, temp_c, humidity_pct, shock_g, door_open,
             battery_pct, flight_status, customs_status, eta_delta_hrs,
             ambient_temp_c, route_alerts, airport_conditions, carrier_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["reading_id"], data["shipment_id"], data["timestamp"],
            data.get("elapsed_hrs"), data.get("leg"), data.get("leg_mode"),
            data.get("lat"), data.get("lng"), data.get("speed_kmh"),
            data.get("temp_c"), data.get("humidity_pct"), data.get("shock_g"),
            1 if data.get("door_open") else 0, data.get("battery_pct"),
            data.get("flight_status"), data.get("customs_status"),
            data.get("eta_delta_hrs"), data.get("ambient_temp_c"),
            data.get("route_alerts"), data.get("airport_conditions"),
            data.get("carrier_id"),
        ))
        self.conn.commit()

    def get_recent_readings(self, shipment_id: str, limit: int = 10) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM telemetry
            WHERE shipment_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (shipment_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ── Anomaly Events ──

    def insert_anomaly_event(self, data: dict) -> str:
        event_id = data.get("event_id", str(uuid.uuid4())[:8])
        self.conn.execute("""
            INSERT INTO anomaly_events
            (event_id, shipment_id, reading_id, anomaly_type, severity, details)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            event_id, data["shipment_id"], data.get("reading_id"),
            data["anomaly_type"], data["severity"],
            json.dumps(data.get("details", {})),
        ))
        self.conn.commit()
        return event_id

    # ── Risk Assessments ──

    def insert_risk_assessment(self, data: dict) -> str:
        assessment_id = data.get("assessment_id", str(uuid.uuid4())[:8])
        self.conn.execute("""
            INSERT INTO risk_assessments
            (assessment_id, anomaly_event_id, shipment_id, spoilage_prob,
             action_window_hrs, delay_budget_hrs, shipment_viable,
             rag_citation, risk_narrative, recommended_action)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            assessment_id, data.get("anomaly_event_id"), data["shipment_id"],
            data.get("spoilage_prob"), data.get("action_window_hrs"),
            data.get("delay_budget_hrs"), 1 if data.get("shipment_viable", True) else 0,
            data.get("rag_citation"), data.get("risk_narrative"),
            data.get("recommended_action"),
        ))
        self.conn.commit()
        return assessment_id

    # ── Agent Decisions ──

    def insert_decision(self, data: dict) -> str:
        decision_id = data.get("decision_id", str(uuid.uuid4())[:8])
        self.conn.execute("""
            INSERT INTO agent_decisions
            (decision_id, risk_assessment_id, shipment_id, cascade_triggered,
             action_plan, decision, approved_by, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            decision_id, data.get("risk_assessment_id"), data["shipment_id"],
            1 if data.get("cascade_triggered") else 0,
            json.dumps(data.get("action_plan", {})),
            data.get("decision"), data.get("approved_by"), data.get("approved_at"),
        ))
        self.conn.commit()
        return decision_id

    # ── Audit Logs ──

    def insert_audit_log(self, data: dict) -> str:
        log_id = data.get("log_id", str(uuid.uuid4())[:8])
        self.conn.execute("""
            INSERT INTO audit_logs
            (log_id, decision_id, shipment_id, action_type, outcome,
             state_snapshot, compliance_flags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id, data.get("decision_id"), data["shipment_id"],
            data["action_type"], data.get("outcome"),
            json.dumps(data.get("state_snapshot", {})),
            json.dumps(data.get("compliance_flags", [])),
        ))
        self.conn.commit()
        return log_id

    def get_audit_trail(self, shipment_id: str) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM audit_logs
            WHERE shipment_id = ?
            ORDER BY created_at
        """, (shipment_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── Utility ──

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def load_synthetic_data():
    """Load generated synthetic data into SQLite."""
    data_dir = Path(__file__).parent

    db = CargoDatabase()
    db.init_tables()

    with open(data_dir / "shipments.json") as f:
        shipments = json.load(f)
    for s in shipments:
        db.insert_shipment(s)
    print(f"Loaded {len(shipments)} shipments into SQLite")

    with open(data_dir / "telemetry.json") as f:
        telemetry = json.load(f)
    for r in telemetry:
        db.insert_telemetry_reading(r)
    print(f"Loaded {len(telemetry)} telemetry readings into SQLite")

    db.close()
    print(f"Database: {DB_PATH}")


if __name__ == "__main__":
    load_synthetic_data()
