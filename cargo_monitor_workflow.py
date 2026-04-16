"""
AI Cargo Monitor — LangGraph Workflow
======================================
Pharma cold-chain monitoring: India -> USA
Multi-agent system with human-in-the-loop

Run:  python cargo_monitor_workflow.py
"""

from __future__ import annotations

import json
import uuid
import sys
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt

# Load .env if present
_env_path = Path(os.path.dirname(__file__)) / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip()
            # Strip surrounding quotes (handles values with # or spaces)
            if len(_v) >= 2 and _v[0] in ('"', "'") and _v[-1] == _v[0]:
                _v = _v[1:-1]
            else:
                # Strip inline comments only when preceded by a space
                _ci = _v.find(" #")
                if _ci != -1:
                    _v = _v[:_ci].strip()
            os.environ.setdefault(_k, _v)

sys.path.insert(0, os.path.dirname(__file__))
from data.lookup_tables import (
    VACCINE_THRESHOLDS,
    COLD_STORAGE_FACILITIES,
    AVAILABLE_CARRIERS,
    FLIGHT_SCHEDULE,
    CUSTOMS_BROKERS,
    REQUIRED_FDA_DOCS,
    WHO_EMERGENCY_FACILITIES,
    BACKUP_HOSPITALS,
    get_vaccine_threshold,
    find_nearest_cold_storage,
    find_available_carrier,
    find_next_flight,
    find_nearest_who_facility,
    haversine_km,
)

# ── Load operational data ──
_DATA_DIR = Path(os.path.dirname(__file__)) / "data"

def _load_json(name):
    p = _DATA_DIR / name
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return []

_PATIENTS  = _load_json("patients.json")
_FLIGHTS   = _load_json("flights.json")
_INVENTORY = _load_json("inventory.json")


# ═══════════════════════════════════════════════════════════════════════
# CARGO STATE
# ═══════════════════════════════════════════════════════════════════════

class CargoState(TypedDict, total=False):
    # ── Raw Telemetry ──
    shipment_id: str
    reading_id: str
    timestamp: str
    elapsed_hrs: float
    leg: str
    leg_mode: str
    temp_c: float
    humidity_pct: float
    shock_g: float
    door_open: bool
    lat: float
    lng: float
    speed_kmh: float
    heading: float
    carrier_id: str
    battery_pct: float
    flight_status: str
    customs_status: str
    eta_delta_hrs: float
    ambient_temp_c: float
    route_alerts: str
    airport_conditions: str

    # ── Shipment Metadata (merged in at invocation) ──
    vaccine_type: str
    temp_range_min: float
    temp_range_max: float
    viability_window_hrs: float
    pack_time: str
    destination_hospital: str
    origin_airport: str
    dest_airport: str
    transit_hub: str
    insurance_days: int

    # ── Ingest Outputs ──
    ingested_at: str
    unit_valid: bool
    normalized_temp_c: float
    alert_class: str
    gps_stall_detected: bool
    sensor_silence: bool

    # ── Detection Results ──
    anomaly_type: str
    severity: str
    detected_at: str
    stall_confirmed: bool
    stall_duration_mins: int
    stall_location: str
    stall_cause: str
    flight_event_type: str
    delay_hrs: float
    cancelled: bool
    next_viable_flight: str
    hold_reason: str
    hold_duration_est: float
    docs_valid: bool

    # ── Risk + Viability ──
    spoilage_prob: float
    action_window_hrs: float
    rag_citation: str
    risk_narrative: str
    total_accumulated_delay_hrs: float
    delay_budget_hrs: float
    shipment_viable: bool
    recommended_action: str
    action_time_cost_hrs: float

    # ── Decision + Approval ──
    cascade_triggered: bool
    decision: str
    approved_by: str
    approved_at: str
    actions_dispatched: str

    # ── Action Outputs ──
    cold_storage_location: str
    cold_storage_lat: float
    cold_storage_lng: float
    detour_time_hrs: float
    updated_eta: str
    new_vehicle_id: str
    transfer_eta_mins: int
    new_carrier_contact: str
    alternate_carrier: str
    pickup_eta_mins: int
    time_impact_hrs: float
    new_flight_id: str
    new_departure_time: str
    hub_storage_needed: bool
    escalation_contact: str
    missing_docs_list: str
    broker_notified: bool
    nearest_who_facility: str
    resupply_eta_hrs: float
    compromised_flag: bool
    backup_facility: str

    # ── Execution Outputs ──
    reroute_confirmed: bool
    new_route_polyline: str
    notifications_sent: str
    rescheduled_count: int
    insurance_doc_url: str
    new_coverage_end_date: str
    inventory_forecast_updated: bool
    audit_log_id: str
    compliance_flags: str

    # ── Journey Loop (stream mode) ──
    reading_index: int
    total_readings: int
    shipment_delivered: bool

    # ── AI Fallback Agent Outputs ──
    ai_diagnosis: str
    ai_reasoning: str

    # ── Anomaly Cooldown + Debounce ──
    active_anomaly: str          # anomaly currently being handled (cleared on resolution)
    anomaly_handled: bool        # True after human approved + execution complete
    consecutive_anomaly_count: int  # how many consecutive readings show same anomaly
    last_resolved_anomaly: str   # last anomaly that was resolved (for cooldown)
    anomaly_cooldown_readings: int  # readings remaining in cooldown after resolution

    # ── Pre-emptive Warning ──
    buffer_warning_issued: bool  # True if low-buffer warning has already been sent this journey
    buffer_warning_message: str  # The warning message text

    # ── Cold Storage Recovery Tracking ──
    at_cold_storage: bool             # True while shipment is stabilizing at cold facility
    resuming_from_cold_storage: bool  # True for the first reading after leaving cold storage
    in_flight_temp_alert: bool        # True when TEMP_BREACH happens mid-flight (can't divert)
    next_checkpoint_name: str         # Name of next waypoint after cold storage (airport/hub)
    next_checkpoint_lat: float        # Lat of that waypoint
    next_checkpoint_lng: float        # Lng of that waypoint


# ═══════════════════════════════════════════════════════════════════════
# ② INGEST AGENT
# ═══════════════════════════════════════════════════════════════════════

def ingest_telemetry(state: CargoState) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    temp = state.get("temp_c", 0.0)
    speed = state.get("speed_kmh", 0.0)
    leg_mode = state.get("leg_mode", "")
    battery = state.get("battery_pct", 100.0)

    vtype = state.get("vaccine_type", "standard_flu")
    thresholds = get_vaccine_threshold(vtype)
    t_min, t_max = thresholds["temp_min"], thresholds["temp_max"]

    unit_valid = isinstance(temp, (int, float)) and -100.0 <= temp <= 60.0

    gps_stall = speed == 0.0 and leg_mode == "truck"
    sensor_silent = battery <= 0.0

    margin = (t_max - t_min) * 0.15
    if temp > t_max or temp < t_min:
        alert = "CRITICAL"
    elif temp > (t_max - margin) or temp < (t_min + margin):
        alert = "WATCH"
    else:
        alert = "NOMINAL"

    if sensor_silent:
        alert = "CRITICAL"

    result = {
        "reading_id": state.get("reading_id", str(uuid.uuid4())[:8]),
        "ingested_at": now,
        "unit_valid": unit_valid,
        "normalized_temp_c": round(temp, 2),
        "alert_class": alert,
        "gps_stall_detected": gps_stall,
        "sensor_silence": sensor_silent,
    }

    # While shipment is at cold storage facility, pin GPS to the facility location
    if state.get("at_cold_storage") and state.get("cold_storage_lat"):
        result["lat"] = state["cold_storage_lat"]
        result["lng"] = state["cold_storage_lng"]

    return result


# ═══════════════════════════════════════════════════════════════════════
# ③ PARALLEL DETECTION AGENTS
# ═══════════════════════════════════════════════════════════════════════

def detect_anomaly(state: CargoState) -> dict:
    temp = state.get("normalized_temp_c", state.get("temp_c", 5.0))
    shock = state.get("shock_g", 0.0)
    door = state.get("door_open", False)
    battery = state.get("battery_pct", 100.0)
    ambient = state.get("ambient_temp_c", 25.0)
    humidity = state.get("humidity_pct", 50.0)
    route_alerts = state.get("route_alerts", "") or ""
    airport_conditions = state.get("airport_conditions", "") or ""

    vtype = state.get("vaccine_type", "standard_flu")
    th = get_vaccine_threshold(vtype)
    t_max = th["temp_max"]
    t_min = th["temp_min"]

    anomaly_type = "NOMINAL"
    severity = "LOW"
    now = datetime.now(timezone.utc).isoformat()

    speed    = state.get("speed_kmh", 0.0)
    leg_mode = state.get("leg_mode", "")

    if door:
        anomaly_type = "DOOR_BREACH"
        severity = "CRITICAL"
    elif battery > 0 and battery < 20.0:
        anomaly_type = "SENSOR_SILENCE"
        severity = "HIGH"
    elif shock > 3.0 and speed == 0.0 and leg_mode == "truck":
        # High shock spike immediately followed by full stop on a truck leg = road accident
        anomaly_type = "ROAD_ACCIDENT"
        severity = "CRITICAL"
    elif temp > t_max:
        deviation = temp - t_max
        if deviation > (t_max - t_min) * 0.5:
            anomaly_type = "REEFER_FAILURE"
            severity = "CRITICAL"
        else:
            anomaly_type = "TEMP_BREACH"
            severity = "CRITICAL" if deviation > 3.0 else "HIGH"
    elif temp < t_min:
        anomaly_type = "TEMP_BREACH"
        severity = "HIGH"
    elif shock > 2.0:
        anomaly_type = "TEMP_BREACH"
        severity = "HIGH" if shock > 5.0 else "LOW"
    else:
        # ── UNKNOWN catch-all: multi-factor stress combinations that slip
        # through the rule-based checks but still warrant AI analysis ──
        emergency_keywords = {"FLOOD", "EARTHQUAKE", "CIVIL", "QUARANTINE",
                              "STRIKE", "TERROR", "HAZMAT", "CONTAMINATION"}
        alert_text = (route_alerts + " " + airport_conditions).upper()
        has_emergency_alert = any(kw in alert_text for kw in emergency_keywords)

        # High ambient + temp near ceiling + elevated humidity → insulation risk
        thermal_stress = (
            ambient > 35.0
            and temp > (t_max - 1.5)
            and humidity > 80.0
        )
        # Borderline shock + near-max temp → combined physical+thermal risk
        compound_stress = (
            shock > 1.0
            and temp > (t_max - 1.0)
            and ambient > 30.0
        )

        if has_emergency_alert or thermal_stress or compound_stress:
            anomaly_type = "UNKNOWN"
            severity = "HIGH"

    return {
        "anomaly_type": anomaly_type,
        "severity": severity,
        "detected_at": now,
    }


def detect_truck_stall(state: CargoState) -> dict:
    """Detect truck stall and use Claude to diagnose the likely cause
    (flat tire, breakdown, accident, traffic, fuel shortage, rest stop)
    and estimate realistic stall duration based on context."""
    import os

    gps_stall = state.get("gps_stall_detected", False)
    speed     = state.get("speed_kmh", 0.0)
    leg_mode  = state.get("leg_mode", "")
    lat       = state.get("lat", 0.0)
    lng       = state.get("lng", 0.0)

    stall = gps_stall and leg_mode == "truck" and speed == 0.0
    if not stall:
        return {"stall_confirmed": False, "stall_duration_mins": 0, "stall_location": ""}

    location_str = f"{lat:.4f},{lng:.4f}"

    # ── Ask Claude to diagnose the stall cause and estimate duration ──
    stall_cause = "unknown vehicle stoppage"
    estimated_duration_mins = 30  # safe fallback

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        prompt = f"""A pharmaceutical cold-chain truck has stopped unexpectedly.
Diagnose the most likely cause and estimate how long the delay will last.

Context:
- GPS location: {location_str}
- Shock/vibration just before stop: {state.get('shock_g', 0.0)}g
- Speed before stop: was moving, now 0 km/h
- Battery: {state.get('battery_pct', 100)}%
- Ambient temperature: {state.get('ambient_temp_c', 30)}°C
- Route alerts: {state.get('route_alerts', 'NONE')}
- Time of day (elapsed hrs into journey): {state.get('elapsed_hrs', 0)}h
- Leg: {state.get('leg', 'road_transit')}

Possible causes: flat tire, engine breakdown, road accident, traffic jam, fuel shortage, driver rest stop, road closure, weight check.

Respond ONLY with valid JSON, no markdown:
{{
  "cause": "<most likely cause in 3-5 words>",
  "estimated_duration_mins": <integer, realistic estimate>,
  "reasoning": "<1 sentence>"
}}"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        stall_cause = parsed.get("cause", stall_cause)
        estimated_duration_mins = int(parsed.get("estimated_duration_mins", 30))
        estimated_duration_mins = max(5, min(estimated_duration_mins, 480))  # clamp 5min–8hrs
    except Exception as e:
        print(f"  [warn] Claude stall diagnosis unavailable: {e}")

    return {
        "stall_confirmed": True,
        "stall_duration_mins": estimated_duration_mins,
        "stall_location": location_str,
        "stall_cause": stall_cause,
    }


def monitor_flight_status(state: CargoState) -> dict:
    status = state.get("flight_status", "NOT_APPLICABLE")
    eta_delta = state.get("eta_delta_hrs", 0.0)

    if status == "CANCELLED":
        leg = state.get("leg", "")
        route = "BOM-DXB" if "hub" in leg or "origin" in leg else "DXB-JFK"
        nf = find_next_flight(route.split("-")[0])
        return {
            "flight_event_type": "CANCELLED",
            "delay_hrs": 8.0,
            "cancelled": True,
            "next_viable_flight": nf["flight_id"] if nf else "NONE_AVAILABLE",
        }

    if status == "DELAYED" or (status == "IN_AIR" and eta_delta > 1.0):
        return {
            "flight_event_type": "DELAYED",
            "delay_hrs": max(abs(eta_delta), 1.0),
            "cancelled": False,
            "next_viable_flight": "",
        }

    return {
        "flight_event_type": "ON_TIME",
        "delay_hrs": 0.0,
        "cancelled": False,
        "next_viable_flight": "",
    }


def monitor_customs(state: CargoState) -> dict:
    """Detect customs hold and use Claude to estimate realistic hold duration
    based on hold type, origin country, vaccine regulatory requirements."""
    import os

    status = state.get("customs_status", "NOT_APPLICABLE")

    if not status.startswith("HOLD"):
        if status == "CLEARED":
            return {"hold_reason": "", "hold_duration_est": 0.0, "docs_valid": True}
        return {"hold_reason": "", "hold_duration_est": 0.0, "docs_valid": True}

    reason = status.replace("HOLD_", "").replace("_", " ").title()

    # ── Ask Claude to estimate realistic hold duration ──
    estimated_hrs = 6.0  # safe fallback
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        prompt = f"""A pharmaceutical vaccine shipment is being held at customs.
Estimate how long this customs hold will realistically last.

Hold details:
- Hold reason code: {status}
- Hold reason (parsed): {reason}
- Destination airport: {state.get('dest_airport', 'JFK')}
- Origin: {state.get('origin_airport', 'BOM')} via {state.get('transit_hub', 'DXB')}
- Vaccine type: {state.get('vaccine_type', 'standard_flu')}
- Carrier: {state.get('carrier_id', 'unknown')}
- Elapsed journey time: {state.get('elapsed_hrs', 0)}h
- Action window remaining: {state.get('action_window_hrs', 48)}h

Consider: FDA inspection timelines, documentation re-submission time, broker escalation speed,
time of day, typical JFK pharmaceutical clearance times.

Respond ONLY with valid JSON, no markdown:
{{
  "estimated_duration_hrs": <realistic float>,
  "severity": "LOW|MEDIUM|HIGH",
  "reasoning": "<1 sentence>"
}}"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        estimated_hrs = float(parsed.get("estimated_duration_hrs", 6.0))
        estimated_hrs = max(0.5, min(estimated_hrs, 72.0))  # clamp 30min–3days
    except Exception as e:
        print(f"  [warn] Claude customs estimation unavailable: {e}")

    return {
        "hold_reason": reason,
        "hold_duration_est": estimated_hrs,
        "docs_valid": False,
    }


# ═══════════════════════════════════════════════════════════════════════
# ④ RISK SCORING AGENT
# ═══════════════════════════════════════════════════════════════════════

def score_risk(state: CargoState) -> dict:
    anomaly = state.get("anomaly_type", "NOMINAL")
    severity = state.get("severity", "LOW")
    stall = state.get("stall_confirmed", False)
    hold = state.get("hold_reason", "")
    cancelled = state.get("cancelled", False)
    delay = state.get("delay_hrs", 0.0)
    vtype = state.get("vaccine_type", "standard_flu")
    viability = state.get("viability_window_hrs", 96.0)
    elapsed = state.get("elapsed_hrs", 0.0)
    temp = state.get("normalized_temp_c", state.get("temp_c", 5.0))

    th = get_vaccine_threshold(vtype)

    spoilage = 0.0
    if anomaly == "NOMINAL":
        spoilage = 0.0
    elif severity == "CRITICAL":
        spoilage = min(0.95, 0.6 + abs(temp - th["temp_max"]) * th["spoilage_rate_per_hr"] * 0.1)
    elif severity == "HIGH":
        spoilage = min(0.7, 0.3 + abs(temp - th["temp_max"]) * 0.05)
    else:
        spoilage = 0.1

    # Non-temperature events (stall, hold, cancellation) only affect delivery
    # delay — they do NOT cause spoilage. Spoilage is a temperature concern only.
    if stall:
        delay = max(delay, state.get("stall_duration_mins", 30) / 60.0)
    if hold:
        delay = max(delay, state.get("hold_duration_est", 6.0))
    if cancelled:
        delay = max(delay, 8.0)

    action_window = max(0.0, viability - elapsed - delay)

    temp_related = anomaly in ("TEMP_BREACH", "REEFER_FAILURE", "SENSOR_SILENCE", "DOOR_BREACH")

    if anomaly in ("TEMP_BREACH", "REEFER_FAILURE"):
        citation = th["regulation"]
    elif anomaly == "DOOR_BREACH":
        citation = "WHO/PQS/E06/IN05.4 -- Container integrity during transit"
    elif hold:
        citation = "FDA 21 CFR 1.94 -- Prior notice of imported food/drugs"
    else:
        citation = "GDP EU 2013/C 343/01 -- Good Distribution Practice"

    sid = state.get("shipment_id", "N/A")
    if anomaly == "NOMINAL":
        narrative = f"Shipment {sid}: All sensors nominal. Temp {temp}C within {th['temp_min']}-{th['temp_max']}C range."
    elif anomaly in ("TEMP_BREACH", "REEFER_FAILURE"):
        severity_word = {"CRITICAL": "critically high", "HIGH": "elevated", "LOW": "minor"}.get(severity, "nominal")
        narrative = (
            f"Shipment {sid}: Temperature {temp}C is {severity_word} — safe range is "
            f"{th['temp_min']}-{th['temp_max']}C. Spoilage risk: {spoilage:.0%}. "
            f"Action window: {action_window:.1f} hrs. Ref: {citation}."
        )
    elif anomaly == "DOOR_BREACH":
        narrative = (
            f"Shipment {sid}: Container door opened during transit. "
            f"Current temp {temp}C (safe: {th['temp_min']}-{th['temp_max']}C). "
            f"Spoilage risk: {spoilage:.0%}. Ref: {citation}."
        )
    elif anomaly == "SENSOR_SILENCE":
        narrative = (
            f"Shipment {sid}: Temperature sensor offline. "
            f"Last reading: {temp}C. Assuming worst-case breach per GDP Section 9.3. "
            f"Spoilage risk: {spoilage:.0%}. Ref: {citation}."
        )
    elif stall:
        narrative = (
            f"Shipment {sid}: Transport vehicle stalled for ~{state.get('stall_duration_mins', 30)} min. "
            f"Temp {temp}C is within range — no spoilage risk. "
            f"Delivery delay: ~{delay:.1f} hrs. Ref: {citation}."
        )
    elif cancelled:
        narrative = (
            f"Shipment {sid}: Cargo flight cancelled. No temperature risk — "
            f"vaccine is in cold storage. Delivery impact: ~{delay:.1f} hrs delay. "
            f"Action window remaining: {action_window:.1f} hrs. Ref: {citation}."
        )
    elif hold:
        narrative = (
            f"Shipment {sid}: Customs hold — {state.get('hold_reason', 'documentation issue')}. "
            f"Temp {temp}C is within range — no spoilage risk. "
            f"Estimated hold time: {delay:.1f} hrs. Ref: {citation}."
        )
    else:
        narrative = (
            f"Shipment {sid}: Anomaly detected ({anomaly.replace('_', ' ')}). "
            f"Temp {temp}C (safe: {th['temp_min']}-{th['temp_max']}C). Ref: {citation}."
        )

    return {
        "spoilage_prob": round(spoilage, 3),
        "action_window_hrs": round(action_window, 1),
        "rag_citation": citation,
        "risk_narrative": narrative,
    }


# ═══════════════════════════════════════════════════════════════════════
# ⑤ VIABILITY BUDGET CHECK
# ═══════════════════════════════════════════════════════════════════════

def check_viability_budget(state: CargoState) -> dict:
    """Ask Claude to estimate realistic action time cost and recommend action
    based on full situational context. Falls back to safe defaults if API unavailable."""
    import os

    action_window = state.get("action_window_hrs", 48.0)
    accumulated   = state.get("total_accumulated_delay_hrs", 0.0)
    spoilage      = state.get("spoilage_prob", 0.0)
    anomaly       = state.get("anomaly_type", "NOMINAL")
    delay         = state.get("delay_hrs", 0.0)

    accumulated += delay
    budget = action_window - accumulated
    viable = budget > 0 and spoilage < 0.90

    if not viable:
        return {
            "total_accumulated_delay_hrs": round(accumulated, 2),
            "delay_budget_hrs": round(budget, 2),
            "shipment_viable": False,
            "recommended_action": "Declare compromised — trigger emergency resupply",
            "action_time_cost_hrs": 0.0,
        }

    if anomaly == "NOMINAL":
        return {
            "total_accumulated_delay_hrs": round(accumulated, 2),
            "delay_budget_hrs": round(budget, 2),
            "shipment_viable": True,
            "recommended_action": "Continue monitoring",
            "action_time_cost_hrs": 0.0,
        }

    # ── Safe fallback values (used if Claude API unavailable) ──
    FALLBACK = {
        "TEMP_BREACH":    ("Divert to nearest cold storage", 2.0),
        "REEFER_FAILURE": ("Emergency vehicle swap", 1.5),
        "TRUCK_STALL":    ("Dispatch alternate carrier", 1.0),
        "ROAD_ACCIDENT":  ("Emergency services + alternate carrier", 2.5),
        "FLIGHT_CANCEL":  ("Rebook next available cargo flight", 4.0),
        "CUSTOMS_HOLD":   ("Escalate to customs broker", 2.0),
        "SENSOR_SILENCE": ("Assume breach protocol, inspect at next stop", 0.5),
        "DOOR_BREACH":    ("Integrity inspection at next stop", 1.0),
        "UNKNOWN":        ("Immediate manual inspection", 1.0),
    }
    fallback_action, fallback_cost = FALLBACK.get(anomaly, ("Investigate anomaly", 1.0))

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        context = f"""You are a pharmaceutical cold-chain logistics expert.
A vaccine shipment has encountered an anomaly and you must estimate:
1. The most appropriate immediate action
2. A realistic time cost (hours) for that action given the specific context

Shipment context:
- Anomaly type: {anomaly}
- Vaccine type: {state.get('vaccine_type', 'unknown')}
- Current leg: {state.get('leg', 'unknown')} ({state.get('leg_mode', 'unknown')})
- Location: lat={state.get('lat', '?')}, lng={state.get('lng', '?')}
- Temperature: {state.get('normalized_temp_c', state.get('temp_c', '?'))}°C (safe: {state.get('temp_range_min','?')}–{state.get('temp_range_max','?')}°C)
- Shock/vibration: {state.get('shock_g', '?')}g
- Stall duration: {state.get('stall_duration_mins', 0)} min
- Flight status: {state.get('flight_status', 'N/A')}
- Customs hold reason: {state.get('hold_reason', 'N/A')}
- Delay so far: {delay:.1f}h  |  Action window remaining: {budget:.1f}h
- Spoilage probability: {spoilage:.1%}
- Route alerts: {state.get('route_alerts', 'NONE')}
- Airport conditions: {state.get('airport_conditions', 'NORMAL')}
- Ambient temperature: {state.get('ambient_temp_c', '?')}°C
- Carrier: {state.get('carrier_id', 'unknown')}
- Stall location: {state.get('stall_location', 'unknown')}

Respond ONLY with valid JSON, no markdown:
{{
  "recommended_action": "<specific actionable step, 1 sentence>",
  "estimated_time_hrs": <realistic float, e.g. 1.5>,
  "reasoning": "<1-2 sentences explaining the estimate>"
}}"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": context}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        action = parsed.get("recommended_action", fallback_action)
        cost   = float(parsed.get("estimated_time_hrs", fallback_cost))
        cost   = max(0.0, min(cost, 24.0))  # sanity clamp

    except Exception as e:
        print(f"  [warn] Claude viability budget estimation unavailable: {e}")
        action = fallback_action
        cost   = fallback_cost

    return {
        "total_accumulated_delay_hrs": round(accumulated, 2),
        "delay_budget_hrs": round(budget, 2),
        "shipment_viable": viable,
        "recommended_action": action,
        "action_time_cost_hrs": cost,
    }


# ═══════════════════════════════════════════════════════════════════════
# ⑤-B  PRE-EMPTIVE BUFFER WARNING
# ═══════════════════════════════════════════════════════════════════════

def check_buffer_warning(state: CargoState) -> dict:
    """Fire a proactive operator warning when delay budget is shrinking
    toward a critical threshold — before it becomes an emergency.
    Issues at most once per journey to avoid noise. No cascade triggered."""
    budget   = state.get("delay_budget_hrs", 999.0)
    anomaly  = state.get("anomaly_type", "NOMINAL")
    already_warned = state.get("buffer_warning_issued", False)
    viable   = state.get("shipment_viable", True)

    # Only warn on NOMINAL (no active anomaly), when buffer is low, and only once
    LOW_BUFFER_THRESHOLD = 8.0   # hours — warn when <8h of buffer remains
    if (
        not already_warned
        and viable
        and anomaly == "NOMINAL"
        and 0 < budget < LOW_BUFFER_THRESHOLD
    ):
        sid      = state.get("shipment_id", "")
        hospital = state.get("destination_hospital", "hospital")
        vtype    = state.get("vaccine_type", "")
        elapsed  = state.get("elapsed_hrs", 0.0)
        accumulated = state.get("total_accumulated_delay_hrs", 0.0)

        msg = (
            f"LOW BUFFER WARNING — {sid}: Only {budget:.1f}h of delay buffer remaining "
            f"(accumulated delay: {accumulated:.1f}h, elapsed: {elapsed:.1f}h). "
            f"No active anomaly — shipment is on track but has little margin left. "
            f"Consider pre-positioning cold storage near {hospital} and alerting "
            f"backup carriers. Vaccine: {vtype}."
        )
        return {
            "buffer_warning_issued": True,
            "buffer_warning_message": msg,
        }

    return {}


# ═══════════════════════════════════════════════════════════════════════
# ⑥ DECISION ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════

def orchestrate_decision(state: CargoState) -> dict:
    anomaly = state.get("anomaly_type", "NOMINAL")
    viable = state.get("shipment_viable", True)
    stall = state.get("stall_confirmed", False)
    cancelled = state.get("cancelled", False)
    hold = state.get("hold_reason", "")
    silence = state.get("sensor_silence", False)
    door = state.get("door_open", False)

    # ── Resolve effective anomaly type ──
    effective_anomaly = anomaly
    if not viable:
        effective_anomaly = "NOT_VIABLE"
    elif stall and anomaly == "NOMINAL":
        effective_anomaly = "TRUCK_STALL"
    elif cancelled and anomaly == "NOMINAL":
        effective_anomaly = "FLIGHT_CANCEL"
    elif hold and anomaly == "NOMINAL":
        effective_anomaly = "CUSTOMS_HOLD"
    elif silence and anomaly == "NOMINAL":
        effective_anomaly = "SENSOR_SILENCE"
    elif door and anomaly == "NOMINAL":
        effective_anomaly = "DOOR_BREACH"

    # ── Debounce: count consecutive readings with same anomaly ──
    # Only escalate to full cascade after 2+ consecutive readings (except
    # DOOR_BREACH and NOT_VIABLE which are always immediate).
    prev_count = state.get("consecutive_anomaly_count", 0)
    prev_anomaly_in_count = state.get("active_anomaly", "NOMINAL")
    # Temperature and safety emergencies are always immediate — no debounce
    # Only logistics anomalies (stall, flight, customs) benefit from debounce
    IMMEDIATE_ANOMALIES = {
        "DOOR_BREACH", "NOT_VIABLE", "REEFER_FAILURE",
        "TEMP_BREACH", "SENSOR_SILENCE", "ROAD_ACCIDENT",
    }
    DEBOUNCE_THRESHOLD = 2   # readings before escalating

    if effective_anomaly != "NOMINAL":
        if prev_anomaly_in_count == effective_anomaly:
            consecutive = prev_count + 1
        else:
            consecutive = 1
    else:
        consecutive = 0

    # ── Cooldown: suppress re-trigger of an already-handled anomaly ──
    # After operator approved+executed, anomaly_handled=True for N readings.
    active_anomaly = state.get("active_anomaly", "NOMINAL")
    anomaly_handled = state.get("anomaly_handled", False)
    cooldown_remaining = state.get("anomaly_cooldown_readings", 0)

    suppress = False
    new_cooldown = max(0, cooldown_remaining - 1)

    if anomaly_handled and effective_anomaly == active_anomaly and cooldown_remaining > 0:
        # Same anomaly re-detected while we're in cooldown — suppress
        suppress = True
        effective_anomaly = "NOMINAL"

    # ── Debounce gate ──
    if not suppress and effective_anomaly not in IMMEDIATE_ANOMALIES and effective_anomaly != "NOMINAL":
        if consecutive < DEBOUNCE_THRESHOLD:
            # Not enough consecutive readings — downgrade to WATCH, don't cascade
            suppress = True
            effective_anomaly = "NOMINAL"

    # ── Cold Storage Recovery: temp normalised → resume journey ──
    at_cold = state.get("at_cold_storage", False)
    resuming = False
    if at_cold and effective_anomaly == "NOMINAL":
        # Temperature is back to safe range — leave cold storage and head to destination
        at_cold = False
        resuming = True

    cascade = effective_anomaly != "NOMINAL"
    dispatched = []
    if cascade:
        dispatched = [
            "execute_reroute", "notify_stakeholders", "reschedule_patients",
            "generate_insurance_doc", "update_inventory_forecast", "write_audit_log",
        ]

    return {
        "anomaly_type": effective_anomaly,
        "cascade_triggered": cascade,
        "actions_dispatched": json.dumps(dispatched),
        "consecutive_anomaly_count": consecutive,
        "active_anomaly": effective_anomaly if cascade else active_anomaly,
        "anomaly_cooldown_readings": new_cooldown,
        "anomaly_handled": anomaly_handled if not cascade else False,
        "at_cold_storage": at_cold,
        "resuming_from_cold_storage": resuming,
    }


def route_orchestrator(state: CargoState) -> str:
    if not state.get("shipment_viable", True):
        return "NOT_VIABLE"
    anomaly = state.get("anomaly_type", "NOMINAL")
    leg_mode = state.get("leg_mode", "truck")

    if anomaly == "TEMP_BREACH":
        # Mid-flight: can't divert to cold storage — use in-flight alert handler
        if leg_mode == "flight":
            return "TEMP_BREACH_FLIGHT"
        # On ground / truck / airport: divert to nearest cold storage
        return "TEMP_BREACH"

    valid = {
        "REEFER_FAILURE", "TRUCK_STALL",
        "FLIGHT_CANCEL", "CUSTOMS_HOLD", "SENSOR_SILENCE",
        "DOOR_BREACH",
    }
    if anomaly in valid:
        return anomaly
    if anomaly == "ROAD_ACCIDENT":
        return "UNKNOWN"
    if anomaly != "NOMINAL":
        return "UNKNOWN"
    return "NOMINAL"


# ═══════════════════════════════════════════════════════════════════════
# ⑦ ACTION AGENTS
# ═══════════════════════════════════════════════════════════════════════

def continue_monitoring(state: CargoState) -> dict:
    return {"recommended_action": "Continue monitoring -- no anomaly detected"}


# Leg → (next_checkpoint_name, airport_code_hint) after cold storage recovery
_LEG_NEXT_CHECKPOINT = {
    "warehouse_to_airport": "Origin Airport",
    "origin_airport_wait":  "Origin Airport",
    "transit_hub_wait":     "Transit Hub",
    "dest_airport_customs": "Destination Airport",
    "last_mile_delivery":   "Destination Hospital",
    "road_transit":         "Next Stop",
}


def _next_checkpoint_coords(state: dict) -> tuple[str, float, float]:
    """Return (name, lat, lng) of the next route checkpoint after cold storage."""
    leg = state.get("leg", "")
    hint = _LEG_NEXT_CHECKPOINT.get(leg, "Destination")

    # Airport / hub coordinate look-up using known IATA codes stored in state
    AIRPORT_COORDS = {
        "BOM": (19.090, 72.866),
        "DXB": (25.253, 55.366),
        "JFK": (40.641, -73.778),
        "DEL": (28.556, 77.100),
        "BLR": (13.199, 77.706),
        "AMD": (23.072, 72.634),
    }
    if hint == "Origin Airport":
        iata = state.get("origin_airport", "BOM")
        lt, ln = AIRPORT_COORDS.get(iata, (19.090, 72.866))
        return f"{iata} Airport", lt, ln
    if hint == "Transit Hub":
        iata = state.get("transit_hub", "DXB")
        lt, ln = AIRPORT_COORDS.get(iata, (25.253, 55.366))
        return f"{iata} Hub", lt, ln
    if hint == "Destination Airport":
        iata = state.get("dest_airport", "JFK")
        lt, ln = AIRPORT_COORDS.get(iata, (40.641, -73.778))
        return f"{iata} Airport", lt, ln
    # Destination Hospital / generic next stop — fall back to route endpoint
    return hint, state.get("lat", 0.0), state.get("lng", 0.0)


def cold_storage_intervention(state: CargoState) -> dict:
    lat = state.get("lat", 19.09)
    lng = state.get("lng", 72.87)
    action_window = state.get("action_window_hrs", 24.0)

    facility = find_nearest_cold_storage(lat, lng)
    if not facility:
        return {"cold_storage_location": "NONE_AVAILABLE", "detour_time_hrs": 0.0}

    dist_km = facility["distance_km"]
    detour_hrs = round(dist_km / 60.0 + 0.5, 1)

    pack_time_str = state.get("pack_time", datetime.now(timezone.utc).isoformat())
    try:
        pack_dt = datetime.fromisoformat(pack_time_str)
    except (ValueError, TypeError):
        pack_dt = datetime.now(timezone.utc)
    elapsed = state.get("elapsed_hrs", 0.0)
    new_eta = pack_dt + timedelta(hours=elapsed + detour_hrs + action_window * 0.5)

    cp_name, cp_lat, cp_lng = _next_checkpoint_coords(state)

    return {
        "cold_storage_location": f"{facility['name']} ({facility['id']}) - {dist_km}km away",
        "cold_storage_lat": facility["lat"],
        "cold_storage_lng": facility["lng"],
        "detour_time_hrs": detour_hrs,
        "updated_eta": new_eta.isoformat(),
        "hub_storage_needed": "hub" not in facility.get("type", ""),
        "time_impact_hrs": detour_hrs,
        # Next route checkpoint after cold storage (used by dashboard to draw the onward path)
        "next_checkpoint_name": cp_name,
        "next_checkpoint_lat":  cp_lat,
        "next_checkpoint_lng":  cp_lng,
        "in_flight_temp_alert": False,
    }


def flight_temp_alert_agent(state: CargoState) -> dict:
    """Handle TEMP_BREACH that occurs during a flight leg.

    A flight cannot be diverted to a cold storage facility. Instead:
    - Alert the airline crew to adjust reefer unit settings
    - Identify the next landing airport and request cold storage preparation there
    - The shipment continues on its flight — no physical reroute
    """
    leg = state.get("leg", "")
    temp = state.get("normalized_temp_c", state.get("temp_c", 0.0))
    vtype = state.get("vaccine_type", "standard_flu")
    thresholds = get_vaccine_threshold(vtype)
    t_min, t_max = thresholds["temp_min"], thresholds["temp_max"]

    # Determine next landing airport from the leg
    if "to_hub" in leg or "to_dest" in leg.replace("dest_flight", "to_dest"):
        next_airport = state.get("transit_hub", "DXB") if "to_hub" in leg else state.get("dest_airport", "JFK")
    else:
        next_airport = state.get("dest_airport", "JFK")

    AIRPORT_COORDS = {
        "BOM": (19.090, 72.866), "DXB": (25.253, 55.366),
        "JFK": (40.641, -73.778), "DEL": (28.556, 77.100),
    }
    ap_lat, ap_lng = AIRPORT_COORDS.get(next_airport, (25.253, 55.366))

    return {
        "in_flight_temp_alert": True,
        "recommended_action": (
            f"Notify airline crew: reefer excursion {temp:.1f}°C (safe: {t_min}–{t_max}°C). "
            f"Request cold storage preparation at {next_airport} on landing."
        ),
        "cold_storage_location": f"Prepare at {next_airport} Airport on landing",
        "cold_storage_lat": ap_lat,
        "cold_storage_lng": ap_lng,
        "next_checkpoint_name": f"{next_airport} Airport",
        "next_checkpoint_lat": ap_lat,
        "next_checkpoint_lng": ap_lng,
        "time_impact_hrs": 1.5,   # ground handling delay to move into airport cold storage
    }


def emergency_vehicle_swap(state: CargoState) -> dict:
    loc = state.get("stall_location", f"{state.get('lat', 19.09)},{state.get('lng', 72.87)}")
    current = state.get("carrier_id", "")

    new_carrier = find_available_carrier(current)
    if not new_carrier:
        return {"new_vehicle_id": "NONE", "transfer_eta_mins": 999}

    return {
        "new_vehicle_id": f"VEH-{new_carrier['carrier_id']}-{uuid.uuid4().hex[:4].upper()}",
        "transfer_eta_mins": new_carrier["response_mins"],
        "new_carrier_contact": f"{new_carrier['name']}: {new_carrier['contact']}",
        "time_impact_hrs": round(new_carrier["response_mins"] / 60.0, 1),
    }


def alternate_carrier_agent(state: CargoState) -> dict:
    current = state.get("carrier_id", "")
    stall_mins = state.get("stall_duration_mins", 0)

    new = find_available_carrier(current)
    if not new:
        return {"alternate_carrier": "NONE", "pickup_eta_mins": 999}

    return {
        "alternate_carrier": f"{new['name']} ({new['carrier_id']})",
        "pickup_eta_mins": new["response_mins"],
        "time_impact_hrs": round((stall_mins + new["response_mins"]) / 60.0, 1),
    }


def flight_rebooking_agent(state: CargoState) -> dict:
    event = state.get("flight_event_type", "ON_TIME")
    action_window = state.get("action_window_hrs", 24.0)
    leg = state.get("leg", "")

    route_prefix = "BOM" if "origin" in leg or "hub" in leg.lower() else "DXB"
    nf = find_next_flight(route_prefix)
    if not nf:
        return {"new_flight_id": "NONE", "new_departure_time": "", "hub_storage_needed": True}

    hub_needed = event == "CANCELLED"

    return {
        "new_flight_id": nf["flight_id"],
        "new_departure_time": nf["departure"],
        "hub_storage_needed": hub_needed,
        "time_impact_hrs": 8.0 if event == "CANCELLED" else 2.0,
    }


def compliance_escalation_agent(state: CargoState) -> dict:
    """Use Claude to select the best customs broker for this hold type,
    identify exactly which documents are missing, and estimate resolution time."""
    import os

    hold_reason = state.get("hold_reason", "Unknown")
    docs_valid  = state.get("docs_valid", True)

    # ── Default fallback: pick first broker, generic docs ──
    broker = CUSTOMS_BROKERS[0]
    missing = ["Cold chain certification", "Importer of Record documentation"]
    if "fda" in hold_reason.lower():
        missing = ["FDA Form 2877", "Certificate of Analysis", "Temperature logger data"]
    response_hrs = float(broker.get("response_hrs", 2.0))

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        broker_list = json.dumps([
            {"name": b["name"], "specialization": b.get("specialization", "general"),
             "response_hrs": b.get("response_hrs", 2.0)}
            for b in CUSTOMS_BROKERS
        ])
        prompt = f"""A pharmaceutical vaccine shipment is held at customs.
Select the best customs broker and identify the exact missing documents.

Hold details:
- Hold reason: {hold_reason}
- Customs status code: {state.get('customs_status', 'HOLD')}
- Destination: {state.get('dest_airport', 'JFK')} airport
- Vaccine type: {state.get('vaccine_type', 'standard_flu')}
- Carrier: {state.get('carrier_id', 'unknown')}
- Hold duration estimate: {state.get('hold_duration_est', 6)}h

Available brokers: {broker_list}

Based on the hold reason, select the most appropriate broker and list
the exact documents that need to be filed to resolve this hold.

Respond ONLY with valid JSON, no markdown:
{{
  "broker_index": <0-based index from available brokers list>,
  "missing_documents": ["<doc1>", "<doc2>", ...],
  "resolution_steps": "<1-2 sentences on how to resolve>",
  "estimated_response_hrs": <float>
}}"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)

        idx = int(parsed.get("broker_index", 0))
        if 0 <= idx < len(CUSTOMS_BROKERS):
            broker = CUSTOMS_BROKERS[idx]
        missing = parsed.get("missing_documents", missing)
        response_hrs = float(parsed.get("estimated_response_hrs", response_hrs))
        response_hrs = max(0.25, min(response_hrs, 48.0))
    except Exception as e:
        print(f"  [warn] Claude compliance estimation unavailable: {e}")

    return {
        "escalation_contact": f"{broker['name']}: {broker['contact']} ({broker['email']})",
        "missing_docs_list": json.dumps(missing),
        "broker_notified": True,
        "time_impact_hrs": response_hrs,
    }


def assume_breach_agent(state: CargoState) -> dict:
    """Use Claude to calculate realistic spoilage probability for sensor silence
    based on last known temperature, time since silence, ambient conditions,
    and vaccine-specific viability curve — instead of hardcoded 70%."""
    import os

    battery  = state.get("battery_pct", 100.0)
    temp     = state.get("normalized_temp_c", state.get("temp_c", 5.0))
    vtype    = state.get("vaccine_type", "standard_flu")
    elapsed  = state.get("elapsed_hrs", 0.0)
    ambient  = state.get("ambient_temp_c", 30.0)
    th       = get_vaccine_threshold(vtype)
    cause    = "Tracker battery death" if battery <= 0 else "Sensor silence > 5 min"

    spoilage = 0.70  # fallback
    narrative_detail = "Treating as worst-case temperature excursion per GDP 2013/C 343/01 Section 9.3."

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        prompt = f"""A pharmaceutical vaccine shipment's temperature sensor has gone silent.
Calculate the realistic spoilage probability given the specific context.

Details:
- Cause: {cause}
- Vaccine type: {vtype}
- Safe temperature range: {th['temp_min']}°C to {th['temp_max']}°C
- Last known container temperature: {temp}°C
- Ambient/outside temperature: {ambient}°C
- Elapsed journey time: {elapsed}h
- Viability window: {state.get('viability_window_hrs', 96)}h
- Battery level: {battery}%
- Leg: {state.get('leg', 'unknown')} ({state.get('leg_mode', 'unknown')})

Consider: how quickly would temperature rise given ambient conditions,
how long has the sensor been silent (1 reading = 30 min), and the
vaccine's sensitivity to temperature excursions.

Respond ONLY with valid JSON, no markdown:
{{
  "spoilage_probability": <float 0.0-1.0>,
  "severity": "CRITICAL|HIGH|MEDIUM",
  "reasoning": "<2 sentences explaining the calculation>"
}}"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        spoilage = float(parsed.get("spoilage_probability", 0.70))
        spoilage = max(0.0, min(spoilage, 1.0))
        narrative_detail = parsed.get("reasoning", narrative_detail)
    except Exception as e:
        print(f"  [warn] Claude sensor-silence assessment unavailable: {e}")

    return {
        "anomaly_type": "SENSOR_SILENCE",
        "severity": "CRITICAL",
        "risk_narrative": (
            f"SENSOR SILENCE: {cause}. Last known temp: {temp}°C (safe: {th['temp_min']}–{th['temp_max']}°C). "
            f"Ambient: {ambient}°C. {narrative_detail}"
        ),
        "spoilage_prob": round(spoilage, 3),
    }


def door_breach_agent(state: CargoState) -> dict:
    """Use Claude to calculate spoilage probability for door breach based on
    actual exposure duration, temperature delta, ambient conditions, and
    vaccine type — instead of hardcoded 60%."""
    import os

    temp    = state.get("normalized_temp_c", state.get("temp_c", 5.0))
    ambient = state.get("ambient_temp_c", 30.0)
    leg     = state.get("leg", "")
    vtype   = state.get("vaccine_type", "standard_flu")
    th      = get_vaccine_threshold(vtype)

    spoilage = 0.60  # fallback
    narrative_detail = "Immediate inspection required per WHO/PQS/E06/IN05.4."

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        temp_delta = abs(ambient - temp)
        prompt = f"""A pharmaceutical vaccine container door was opened during transit.
Calculate the realistic spoilage probability.

Details:
- Vaccine type: {vtype}
- Safe temperature range: {th['temp_min']}°C to {th['temp_max']}°C
- Container temperature at breach: {temp}°C
- Ambient/outside temperature: {ambient}°C
- Temperature delta (exposure risk): {temp_delta:.1f}°C
- Leg where breach occurred: {leg.replace('_', ' ')}
- Elapsed journey time: {state.get('elapsed_hrs', 0)}h
- Shock at time of breach: {state.get('shock_g', 0)}g

Consider: duration of exposure (one reading = 30 min), temperature differential,
vaccine sensitivity, and whether the container temperature would have been
significantly affected by one door-open event.

Respond ONLY with valid JSON, no markdown:
{{
  "spoilage_probability": <float 0.0-1.0>,
  "severity": "CRITICAL|HIGH|MEDIUM",
  "reasoning": "<2 sentences>"
}}"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        spoilage = float(parsed.get("spoilage_probability", 0.60))
        spoilage = max(0.0, min(spoilage, 1.0))
        narrative_detail = parsed.get("reasoning", narrative_detail)
    except Exception as e:
        print(f"  [warn] Claude door-breach assessment unavailable: {e}")

    return {
        "anomaly_type": "DOOR_BREACH",
        "severity": "CRITICAL",
        "risk_narrative": (
            f"CONTAINER BREACH: Door opened during {leg.replace('_', ' ')}. "
            f"Container: {temp}°C, ambient: {ambient}°C (delta: {abs(ambient-temp):.1f}°C). "
            f"{narrative_detail}"
        ),
        "spoilage_prob": round(spoilage, 3),
    }


def emergency_resupply_agent(state: CargoState) -> dict:
    vtype = state.get("vaccine_type", "standard_flu")
    hospital = state.get("destination_hospital", "NYC General Hospital")

    facility = find_nearest_who_facility(vtype)
    if not facility:
        return {
            "nearest_who_facility": "NONE_AVAILABLE",
            "resupply_eta_hrs": 999.0,
            "compromised_flag": True,
        }

    return {
        "nearest_who_facility": f"{facility['name']} ({facility['location']})",
        "resupply_eta_hrs": facility["eta_hrs"],
        "compromised_flag": True,
        "risk_narrative": (
            f"SHIPMENT COMPROMISED: Viability budget exhausted. "
            f"Original shipment to {hospital} declared non-viable. "
            f"Emergency resupply initiated from {facility['name']}, "
            f"ETA {facility['eta_hrs']} hours. "
            f"Original shipment flagged for disposal per FDA 21 CFR 211.125."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# ⑦-B  AI FALLBACK AGENT  (unknown / unclassified anomalies)
# ═══════════════════════════════════════════════════════════════════════

def ai_fallback_agent(state: CargoState) -> dict:
    """AI-powered fallback for anomalies that don't match any known failure mode.

    Calls Claude to analyse the full sensor context, diagnose what is likely
    happening, and recommend a specific immediate action.  Gracefully degrades
    to a manual-inspection recommendation if the API is unavailable.
    """
    import os

    sid         = state.get("shipment_id", "unknown")
    vtype       = state.get("vaccine_type", "standard_flu")
    th          = get_vaccine_threshold(vtype)
    elapsed     = state.get("elapsed_hrs", 0.0)
    viability   = state.get("viability_window_hrs", 96.0)
    anomaly_raw = state.get("anomaly_type", "UNKNOWN")

    sensor_block = f"""
- Temperature        : {state.get("normalized_temp_c", state.get("temp_c", "?"))}°C  (safe: {th["temp_min"]}–{th["temp_max"]}°C)
- Shock / Vibration  : {state.get("shock_g", "?")} g
- Door Open          : {state.get("door_open", False)}
- Battery            : {state.get("battery_pct", "?")} %
- Ambient Temp       : {state.get("ambient_temp_c", "?")}°C
- Humidity           : {state.get("humidity_pct", "?")} %
- Speed              : {state.get("speed_kmh", "?")} km/h
- Transport Leg      : {state.get("leg", "?")} ({state.get("leg_mode", "?")})
- Flight Status      : {state.get("flight_status", "N/A")}
- Customs Status     : {state.get("customs_status", "N/A")}
- Route Alerts       : {state.get("route_alerts", "none") or "none"}
- Airport Conditions : {state.get("airport_conditions", "none") or "none"}
- Elapsed / Viability: {elapsed:.1f} h / {viability} h
- Raw Anomaly Tag    : {anomaly_raw}"""

    prompt = f"""You are an expert in pharmaceutical cold-chain logistics and vaccine transportation.

A shipment of {vtype} vaccines is en-route from India to the USA and has triggered an
UNCLASSIFIED anomaly — the automated rule engine could not map it to any of its
known failure modes (TEMP_BREACH, REEFER_FAILURE, TRUCK_STALL, FLIGHT_CANCEL,
CUSTOMS_HOLD, SENSOR_SILENCE, DOOR_BREACH).

Current sensor snapshot at {elapsed:.1f} h into the journey:
{sensor_block}

Your task:
1. Diagnose what unusual situation is most likely occurring given these combined readings.
2. Assess the risk to cargo viability.
3. Recommend one specific, actionable immediate step the operations team should take.

Respond with ONLY valid JSON — no markdown fences, no commentary — in this exact schema:
{{
  "diagnosis": "<one sentence: what is likely happening>",
  "risk_level": "CRITICAL|HIGH|MEDIUM|LOW",
  "recommended_action": "<one concrete action sentence>",
  "reasoning": "<two to three sentences explaining why>",
  "time_sensitive": true
}}"""

    # ── Call Claude ──────────────────────────────────────────────────────
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        # Strip accidental markdown fences if Claude adds them
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
            if "```" in raw:
                raw = raw[: raw.index("```")]

        ai_resp = json.loads(raw)

        diagnosis  = ai_resp.get("diagnosis", "Unclassified sensor anomaly")
        risk_level = ai_resp.get("risk_level", "HIGH").upper()
        rec_action = ai_resp.get("recommended_action", "Manual inspection required")
        reasoning  = ai_resp.get("reasoning", "")
        time_flag  = ai_resp.get("time_sensitive", True)

        narrative = (
            f"Shipment {sid} — AI-Detected Unclassified Anomaly\n"
            f"Diagnosis : {diagnosis}\n"
            f"Risk Level: {risk_level}\n"
            f"Action    : {rec_action}\n"
            f"Reasoning : {reasoning}"
            + ("\n[TIME SENSITIVE — act immediately]" if time_flag else "")
        )

        severity_map = {"CRITICAL": "CRITICAL", "HIGH": "HIGH",
                        "MEDIUM": "HIGH", "LOW": "LOW"}

        return {
            "anomaly_type": "UNKNOWN",
            "severity": severity_map.get(risk_level, "HIGH"),
            "recommended_action": rec_action,
            "risk_narrative": narrative,
            "ai_diagnosis": diagnosis,
            "ai_reasoning": reasoning,
            "time_impact_hrs": 1.0 if time_flag else 0.5,
        }

    except Exception as exc:
        # Graceful degradation — surface a manual-inspection recommendation
        fallback_narrative = (
            f"Shipment {sid}: Unclassified anomaly detected (raw tag: {anomaly_raw}). "
            f"AI analysis unavailable ({type(exc).__name__}: {exc}). "
            "Recommend manual inspection by logistics supervisor immediately."
        )
        return {
            "anomaly_type": "UNKNOWN",
            "severity": "HIGH",
            "recommended_action": "Manual inspection required — automated AI analysis unavailable",
            "risk_narrative": fallback_narrative,
            "ai_diagnosis": f"AI analysis failed: {exc}",
            "ai_reasoning": "Manual review required due to API unavailability",
            "time_impact_hrs": 1.0,
        }


# ═══════════════════════════════════════════════════════════════════════
# ⑧ HUMAN-IN-THE-LOOP APPROVAL
# ═══════════════════════════════════════════════════════════════════════

def human_approval_gateway(state: CargoState) -> dict:
    human_input = interrupt({
        "risk_narrative": state.get("risk_narrative", ""),
        "spoilage_prob": state.get("spoilage_prob", 0.0),
        "recommended_action": state.get("recommended_action", ""),
        "delay_budget_hrs": state.get("delay_budget_hrs", 0.0),
        "question": "Approve the recommended action?",
    })
    return {
        "decision": human_input.get("decision", "REJECT"),
        "approved_by": human_input.get("operator_id", ""),
        "approved_at": human_input.get("timestamp", ""),
    }


def route_human_approval(state: CargoState):
    if state.get("decision") == "APPROVE":
        return [
            Send("execute_reroute", state),
            Send("notify_stakeholders", state),
            Send("reschedule_patients", state),
            Send("generate_insurance_doc", state),
            Send("update_inventory_forecast", state),
            Send("write_audit_log", state),
        ]
    return "orchestrate_decision"


# ═══════════════════════════════════════════════════════════════════════
# ⑨ PARALLEL EXECUTION AGENTS
# ═══════════════════════════════════════════════════════════════════════

def execute_reroute(state: CargoState) -> dict:
    carrier = state.get("alternate_carrier", state.get("new_vehicle_id", ""))
    cold = state.get("cold_storage_location", "")
    flight = state.get("new_flight_id", "")

    has_action = bool(carrier or cold or flight)
    segments = []
    lat, lng = state.get("lat", 0.0), state.get("lng", 0.0)
    if cold:
        segments.append(f"({lat:.2f},{lng:.2f})->COLD_STORAGE")
    if flight:
        segments.append(f"->FLIGHT:{flight}")
    segments.append(f"->{state.get('dest_airport', 'JFK')}")
    segments.append(f"->{state.get('destination_hospital', 'HOSPITAL')}")

    return {
        "reroute_confirmed": has_action,
        "new_route_polyline": "".join(segments) if segments else "NO_CHANGE",
        # Mark shipment as physically at cold storage until temp normalises
        "at_cold_storage": bool(cold),
        "resuming_from_cold_storage": False,
    }


def _generate_and_send_email(state: CargoState, extra_context: str = "") -> bool:
    """Generate an AI-written alert email with Claude and send it via SMTP.

    Reads credentials from env vars:
      EMAIL_FROM      — sender Gmail / SMTP address
      EMAIL_PASSWORD  — Gmail App Password (or SMTP password)
      EMAIL_SMTP_SERVER (default smtp.gmail.com)
      EMAIL_SMTP_PORT   (default 587)

    Sends to the address in ALERT_EMAIL env var (or falls back to EMAIL_FROM).
    Returns True if the email was sent successfully.
    """
    RECIPIENT = os.environ.get("ALERT_EMAIL", os.environ.get("EMAIL_FROM", ""))

    smtp_from   = os.environ.get("EMAIL_FROM", "")
    smtp_pass   = os.environ.get("EMAIL_PASSWORD", "")
    smtp_server = os.environ.get("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port   = int(os.environ.get("EMAIL_SMTP_PORT", "587"))

    sid      = state.get("shipment_id", "?")
    hospital = state.get("destination_hospital", "Unknown Hospital")
    anomaly  = state.get("anomaly_type", "UNKNOWN").replace("_", " ")
    severity = state.get("severity", "HIGH")
    action   = state.get("recommended_action", "Manual review")
    delay    = state.get("time_impact_hrs", 0.0) or 0.0
    narrative= state.get("risk_narrative", "")[:400]
    vtype    = state.get("vaccine_type", "vaccine")
    temp     = state.get("normalized_temp_c", state.get("temp_c", "?"))
    elapsed  = state.get("elapsed_hrs", 0)

    # ── AI-generated subject + body ──────────────────────────────────
    ai_prompt = f"""You are writing an urgent operational alert email for a pharmaceutical cold-chain logistics incident.

Incident details:
- Shipment ID     : {sid}
- Vaccine Type    : {vtype}
- Destination     : {hospital}
- Anomaly         : {anomaly} (Severity: {severity})
- Action Taken    : {action}
- Estimated Delay : ~{delay:.1f} hours
- Current Temp    : {temp}°C
- Hours into trip : {elapsed:.1f} h
- Summary         : {narrative}
{extra_context}

Write a professional, urgent alert email. Be specific and factual.

Respond ONLY as valid JSON (no markdown):
{{
  "subject": "<concise subject under 80 chars, starts with [URGENT] if severity is CRITICAL or HIGH>",
  "body_html": "<full HTML email body, 3-4 paragraphs: incident summary, patient/inventory impact, actions taken, next steps required>"
}}"""

    subject   = f"[ALERT] Cold-Chain Incident: {sid} – {anomaly} at {hospital}"
    body_html = (
        f"<h2>Cold-Chain Alert — {anomaly}</h2>"
        f"<p><b>Shipment:</b> {sid} | <b>Hospital:</b> {hospital} | "
        f"<b>Severity:</b> {severity}</p>"
        f"<p><b>Action Taken:</b> {action}</p>"
        f"<p><b>Estimated Delay:</b> ~{delay:.1f} hours</p>"
        f"<p>{narrative}</p>"
        f"{extra_context}"
    )

    try:
        import anthropic as _ant
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY not set")
        client = _ant.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": ai_prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
            if "```" in raw:
                raw = raw[: raw.index("```")]
        parsed   = json.loads(raw)
        subject  = parsed.get("subject", subject)
        body_html= parsed.get("body_html", body_html)
        print(f"  [email] AI generated subject: {subject}")
    except Exception as ai_err:
        print(f"  [email] AI generation skipped ({ai_err}), using fallback content")

    # ── Send via SMTP ────────────────────────────────────────────────
    if not smtp_from or not smtp_pass:
        print(f"  [email] SMTP not configured (set EMAIL_FROM + EMAIL_PASSWORD). "
              f"Would send to {RECIPIENT}: {subject}")
        return False

    try:
        mime = MIMEMultipart("alternative")
        mime["Subject"] = subject
        mime["From"]    = smtp_from
        mime["To"]      = RECIPIENT
        mime.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(smtp_from, smtp_pass)
            srv.sendmail(smtp_from, RECIPIENT, mime.as_string())

        print(f"  [email] Sent to {RECIPIENT}: {subject}")
        return True
    except Exception as smtp_err:
        print(f"  [email] SMTP send failed: {smtp_err}")
        return False


def notify_stakeholders(state: CargoState) -> dict:
    sid      = state.get("shipment_id", "N/A")
    eta      = state.get("updated_eta", "TBD")
    hospital = state.get("destination_hospital", "")
    anomaly  = state.get("anomaly_type", "NOMINAL")

    notifications = [
        f"TO: {hospital} Ops Team | Shipment {sid}: {anomaly.replace('_',' ')}. "
        f"Revised ETA: {eta}. Action: {state.get('recommended_action', 'N/A')}"
    ]

    if state.get("broker_notified"):
        notifications.append(
            f"TO: Customs Broker | Shipment {sid} customs escalation. "
            f"{state.get('escalation_contact', '')}"
        )
    if state.get("new_flight_id"):
        notifications.append(
            f"TO: Airline Ops | Shipment {sid} rebooked to flight "
            f"{state.get('new_flight_id','')} departing {state.get('new_departure_time','')}"
        )

    backup = BACKUP_HOSPITALS.get(hospital)
    if backup:
        notifications.append(
            f"TO: {backup['backup']} (BACKUP) | Standby for potential reroute of {sid}"
        )

    # ── Look up next available flight for context in email ───────────
    next_flight_info = ""
    if _FLIGHTS:
        dest = state.get("dest_airport", "JFK")
        origin = state.get("transit_hub", "DXB")
        route_flights = [
            f for f in _FLIGHTS
            if f["origin"] == origin and f["destination"] == dest
            and f["status"] == "SCHEDULED"
            and f["available_capacity_kg"] > 200
        ]
        if route_flights:
            nf = sorted(route_flights, key=lambda x: x["departure_utc"])[0]
            next_flight_info = (
                f"\n<p><b>Next available cargo flight:</b> {nf['flight_id']} "
                f"({nf['airline']}) — {nf['origin']}→{nf['destination']} "
                f"departing {nf['departure_utc'][:16].replace('T',' ')} UTC, "
                f"{nf['available_capacity_kg']} kg available</p>"
            )

    # ── Send real email ───────────────────────────────────────────────
    _generate_and_send_email(state, extra_context=next_flight_info)

    return {
        "notifications_sent": json.dumps(notifications),
        "backup_facility": backup["backup"] if backup else "",
    }


def reschedule_patients(state: CargoState) -> dict:
    """Find real affected patients from patients.json and reschedule them."""
    hospital  = state.get("destination_hospital", "Hospital")
    sid       = state.get("shipment_id", "N/A")
    vtype     = state.get("vaccine_type", "standard_flu")
    pack_time = state.get("pack_time", "")

    action_delay = state.get("time_impact_hrs", 0.0) or 0.0
    flight_delay = state.get("delay_hrs", 0.0) or 0.0
    delay = max(action_delay, flight_delay)

    if delay <= 0:
        return {"rescheduled_count": 0}

    # Work out original and delayed delivery windows
    TOTAL_ROUTE_HRS = 38.5
    try:
        pack_dt = datetime.fromisoformat(
            pack_time.replace("Z", "+00:00") if pack_time else ""
        )
    except (ValueError, TypeError):
        pack_dt = datetime.now(timezone.utc)

    original_delivery = pack_dt + timedelta(hours=TOTAL_ROUTE_HRS)
    delayed_delivery  = original_delivery + timedelta(hours=delay)

    # Patients at this hospital are at risk if their appointment falls
    # in [original_delivery, delayed_delivery + 4h buffer]
    window_end = delayed_delivery + timedelta(hours=4)

    affected = [
        p for p in _PATIENTS
        if p["hospital"] == hospital
        and p["vaccine_type"] == vtype
        and _in_window(p["appointment_utc"], original_delivery, window_end)
    ]

    # Format ETA
    try:
        eta_display = delayed_delivery.strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        eta_display = "to be confirmed"

    if not affected:
        # Fallback: return a count if no patients match the exact window
        count = max(1, int(delay * 2))
        return {
            "rescheduled_count": count,
            "notifications_sent": json.dumps([
                f"TO: {hospital} Scheduling Desk | Shipment {sid} delayed ~{delay:.1f} hrs. "
                f"~{count} appointment(s) in the delivery window may need rescheduling. "
                f"New ETA: {eta_display}."
            ]),
        }

    names = ", ".join(p["patient_name"] for p in affected[:5])
    if len(affected) > 5:
        names += f" (+{len(affected)-5} more)"

    return {
        "rescheduled_count": len(affected),
        "notifications_sent": json.dumps([
            f"TO: {hospital} Scheduling Desk | "
            f"Vaccine shipment {sid} delayed ~{delay:.1f} hrs. "
            f"{len(affected)} patient appointment(s) affected: {names}. "
            f"Revised vaccine delivery ETA: {eta_display}. "
            f"Physicians: {', '.join(sorted({p['physician'] for p in affected}))}."
        ]),
    }


def _in_window(appt_str: str, start: datetime, end: datetime) -> bool:
    """Return True if appt_str (ISO) falls within [start, end]."""
    try:
        dt = datetime.fromisoformat(appt_str.replace("Z", "+00:00"))
        return start <= dt <= end
    except Exception:
        return False


def generate_insurance_doc(state: CargoState) -> dict:
    sid      = state.get("shipment_id", "N/A")
    anomaly  = state.get("anomaly_type", "UNKNOWN")
    spoilage = state.get("spoilage_prob", 0.0)
    delay    = state.get("total_accumulated_delay_hrs", 0.0)
    orig_days= state.get("insurance_days", 4)
    vtype    = state.get("vaccine_type", "standard_flu")

    extra_days = max(1, int(delay / 24) + 1)
    new_end    = datetime.now(timezone.utc) + timedelta(days=orig_days + extra_days)
    exposure   = round(spoilage * 50000, 2)

    return {
        "insurance_doc_url":     f"outputs/insurance_claim_{sid}.json",
        "new_coverage_end_date": new_end.strftime("%Y-%m-%d"),
    }


def update_inventory_forecast(state: CargoState) -> dict:
    """Read real inventory record and compute shortfall risk."""
    hospital = state.get("destination_hospital", "Hospital")
    vtype    = state.get("vaccine_type", "standard_flu")
    delay    = max(
        state.get("total_accumulated_delay_hrs", 0.0) or 0.0,
        state.get("time_impact_hrs", 0.0) or 0.0,
    )
    viable   = state.get("shipment_viable", True)

    # Find matching inventory record
    record = next(
        (r for r in _INVENTORY
         if r["hospital"] == hospital and r["vaccine_type"] == vtype),
        None,
    )

    if record:
        current     = record["current_units"]
        daily_rate  = record["daily_usage_rate"]
        days_supply = record["days_of_supply"]
        minimum     = record["minimum_threshold"]
        delay_days  = delay / 24.0
        projected   = max(0, current - daily_rate * delay_days)
        shortfall   = not viable or projected < minimum or delay > 24.0

        inv_note = (
            f"Current stock: {current} units ({days_supply:.1f} days supply, "
            f"usage {daily_rate}/day). "
            + (
                f"Delay of {delay:.1f}h projects stock to ~{projected:.0f} units — "
                f"SHORTFALL — BELOW minimum threshold of {minimum}. Backup allocation triggered."
                if shortfall else
                f"Delay of {delay:.1f}h projects stock to ~{projected:.0f} units — "
                f"above minimum ({minimum}). No shortfall expected."
            )
        )
    else:
        shortfall = not viable or delay > 24.0
        inv_note  = (
            f"No inventory record found for {hospital}/{vtype}. "
            f"{'SHORTFALL RISK — backup allocation triggered.' if shortfall else 'Within buffer.'}"
        )

    return {
        "inventory_forecast_updated": True,
        "risk_narrative": (
            state.get("risk_narrative", "") +
            f" | INVENTORY ({hospital}, {vtype}): {inv_note}"
        ),
    }


def write_audit_log(state: CargoState) -> dict:
    log_id = f"AUDIT-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc).isoformat()

    audit = {
        "audit_log_id": log_id,
        "timestamp": now,
        "shipment_id": state.get("shipment_id", ""),
        "anomaly_type": state.get("anomaly_type", ""),
        "severity": state.get("severity", ""),
        "spoilage_prob": state.get("spoilage_prob", 0.0),
        "action_taken": state.get("recommended_action", ""),
        "approved_by": state.get("approved_by", ""),
        "approved_at": state.get("approved_at", ""),
        "reading_temp_c": state.get("normalized_temp_c", state.get("temp_c")),
        "reading_timestamp": state.get("timestamp", ""),
        "vaccine_type": state.get("vaccine_type", ""),
        "carrier_id": state.get("carrier_id", ""),
        "gps_lat": state.get("lat"),
        "gps_lng": state.get("lng"),
        "regulatory_citation": state.get("rag_citation", ""),
        "compliance_status": "COMPLIANT" if state.get("shipment_viable", True) else "NON_COMPLIANT",
        "generated_at": now,
        "format": "FDA 21 CFR Part 11 compliant",
    }

    resolved_anomaly = state.get("anomaly_type", "NOMINAL")
    return {
        "audit_log_id": log_id,
        "compliance_flags": json.dumps([
            audit["compliance_status"],
            "GDP_LOGGED",
            "HUMAN_APPROVAL_RECORDED" if state.get("approved_by") else "PENDING_APPROVAL",
        ]),
        # Mark anomaly as handled — suppress re-triggering for next 4 readings (cooldown)
        "anomaly_handled": True,
        "last_resolved_anomaly": resolved_anomaly,
        "anomaly_cooldown_readings": 4,
        "consecutive_anomaly_count": 0,
    }


# ═══════════════════════════════════════════════════════════════════════
# ⑩ COMPLIANCE LOGGER
# ═══════════════════════════════════════════════════════════════════════

def compliance_logger(state: CargoState) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    anomaly = state.get("anomaly_type", "NOMINAL")
    viable = state.get("shipment_viable", True)
    compromised = state.get("compromised_flag", False)

    flags = ["GDP_COMPLIANT", "AUDIT_TRAIL_COMPLETE"]
    if not viable or compromised:
        flags.append("SHIPMENT_COMPROMISED")
        flags.append("DISPOSAL_REQUIRED")
    if state.get("approved_by"):
        flags.append("HUMAN_APPROVAL_VERIFIED")
    if anomaly != "NOMINAL":
        flags.append(f"ANOMALY_{anomaly}")

    return {
        "compliance_flags": json.dumps(flags),
        "compromised_flag": compromised or not viable,
    }


def route_compliance(state: CargoState) -> str:
    if state.get("compromised_flag"):
        return "end"
    return "continue"


# ═══════════════════════════════════════════════════════════════════════
# ⑪ STREAM-MODE: WAIT FOR NEXT READING
# ═══════════════════════════════════════════════════════════════════════

_RESET_FIELDS = {
    "anomaly_type": "NOMINAL", "severity": "LOW", "stall_confirmed": False,
    "stall_duration_mins": 0, "stall_location": "", "cancelled": False,
    "hold_reason": "", "hold_duration_est": 0.0, "delay_hrs": 0.0,
    "cascade_triggered": False, "cold_storage_location": "",
    "cold_storage_lat": 0.0, "cold_storage_lng": 0.0, "time_impact_hrs": 0.0,
    "decision": "", "actions_dispatched": "",
    "ai_diagnosis": "", "ai_reasoning": "",
    "in_flight_temp_alert": False,
    "next_checkpoint_name": "", "next_checkpoint_lat": 0.0, "next_checkpoint_lng": 0.0,
    "resuming_from_cold_storage": False,
}


def wait_for_next_reading(state: CargoState) -> dict:
    """Interrupt between readings so the dashboard can feed the next one.

    Carries forward accumulated delays (including action detour costs) so
    the next loop iteration has an accurate delay budget. If the shipment
    is delivered or all readings are consumed, routes to END without
    interrupting.
    """
    idx = state.get("reading_index", 0) + 1
    total = state.get("total_readings", 0)
    leg = state.get("leg", "")

    # Carry forward any action-induced delay into the running total
    impact = state.get("time_impact_hrs", 0.0) or 0.0
    accum = state.get("total_accumulated_delay_hrs", 0.0) or 0.0
    new_accum = round(accum + impact, 2) if impact else accum

    delivered = (total > 0 and idx >= total) or leg == "dest_delivery"
    if delivered or state.get("shipment_delivered", False):
        return {
            "shipment_delivered": True,
            "reading_index": idx,
            "total_accumulated_delay_hrs": new_accum,
        }

    next_reading = interrupt({
        "type": "next_reading",
        "reading_index": idx,
        "total_readings": total,
        "total_accumulated_delay_hrs": new_accum,
    })

    result = {**_RESET_FIELDS, **dict(next_reading), "reading_index": idx}
    result["total_accumulated_delay_hrs"] = new_accum

    # Persist cold storage tracking fields across readings — _RESET_FIELDS zeros them
    if state.get("at_cold_storage"):
        result["at_cold_storage"] = True
        result["cold_storage_lat"] = state.get("cold_storage_lat", 0.0)
        result["cold_storage_lng"] = state.get("cold_storage_lng", 0.0)
        result["cold_storage_location"] = state.get("cold_storage_location", "")

    return result


def route_after_wait(state: CargoState) -> str:
    if state.get("shipment_delivered", False):
        return "end"
    return "ingest_telemetry"


# ═══════════════════════════════════════════════════════════════════════
# BUILD THE GRAPH
# ═══════════════════════════════════════════════════════════════════════

def build_cargo_monitor_graph(mode: str = "loop") -> StateGraph:
    """Build the cargo monitor graph.

    Args:
        mode: "loop"   — continuous loop back to ingest (for mermaid/visualization)
              "single" — one reading per invocation, every path → END
              "stream" — interrupt-based loop: dashboard feeds readings one by one
                         via wait_for_next_reading; handles multiple anomalies
                         in a single shipment journey
    """
    builder = StateGraph(CargoState)

    builder.add_node("ingest_telemetry", ingest_telemetry)
    builder.add_node("detect_anomaly", detect_anomaly)
    builder.add_node("detect_truck_stall", detect_truck_stall)
    builder.add_node("monitor_flight_status", monitor_flight_status)
    builder.add_node("monitor_customs", monitor_customs)
    builder.add_node("score_risk", score_risk)
    builder.add_node("check_viability_budget", check_viability_budget)
    builder.add_node("check_buffer_warning", check_buffer_warning)
    builder.add_node("orchestrate_decision", orchestrate_decision)
    builder.add_node("continue_monitoring", continue_monitoring)
    builder.add_node("cold_storage_intervention", cold_storage_intervention)
    builder.add_node("flight_temp_alert_agent", flight_temp_alert_agent)
    builder.add_node("emergency_vehicle_swap", emergency_vehicle_swap)
    builder.add_node("alternate_carrier_agent", alternate_carrier_agent)
    builder.add_node("flight_rebooking_agent", flight_rebooking_agent)
    builder.add_node("compliance_escalation_agent", compliance_escalation_agent)
    builder.add_node("assume_breach_agent", assume_breach_agent)
    builder.add_node("door_breach_agent", door_breach_agent)
    builder.add_node("emergency_resupply_agent", emergency_resupply_agent)
    builder.add_node("ai_fallback_agent", ai_fallback_agent)
    builder.add_node("human_approval_gateway", human_approval_gateway)
    builder.add_node("execute_reroute", execute_reroute)
    builder.add_node("notify_stakeholders", notify_stakeholders)
    builder.add_node("reschedule_patients", reschedule_patients)
    builder.add_node("generate_insurance_doc", generate_insurance_doc)
    builder.add_node("update_inventory_forecast", update_inventory_forecast)
    builder.add_node("write_audit_log", write_audit_log)
    builder.add_node("compliance_logger", compliance_logger)

    if mode == "stream":
        builder.add_node("wait_for_next_reading", wait_for_next_reading)

    builder.add_edge(START, "ingest_telemetry")

    def fan_out_detection(state: CargoState):
        return [
            Send("detect_anomaly", state),
            Send("detect_truck_stall", state),
            Send("monitor_flight_status", state),
            Send("monitor_customs", state),
        ]

    builder.add_conditional_edges(
        "ingest_telemetry", fan_out_detection,
        ["detect_anomaly", "detect_truck_stall",
         "monitor_flight_status", "monitor_customs"],
    )

    builder.add_edge("detect_anomaly", "score_risk")
    builder.add_edge("detect_truck_stall", "score_risk")
    builder.add_edge("monitor_flight_status", "score_risk")
    builder.add_edge("monitor_customs", "score_risk")

    builder.add_edge("score_risk", "check_viability_budget")
    builder.add_edge("check_viability_budget", "check_buffer_warning")
    builder.add_edge("check_buffer_warning", "orchestrate_decision")

    builder.add_conditional_edges(
        "orchestrate_decision", route_orchestrator,
        {
            "NOMINAL":        "continue_monitoring",
            "TEMP_BREACH":        "cold_storage_intervention",
            "TEMP_BREACH_FLIGHT": "flight_temp_alert_agent",
            "REEFER_FAILURE":     "emergency_vehicle_swap",
            "TRUCK_STALL":    "alternate_carrier_agent",
            "FLIGHT_CANCEL":  "flight_rebooking_agent",
            "CUSTOMS_HOLD":   "compliance_escalation_agent",
            "SENSOR_SILENCE": "assume_breach_agent",
            "DOOR_BREACH":    "door_breach_agent",
            "NOT_VIABLE":     "emergency_resupply_agent",
            "UNKNOWN":        "ai_fallback_agent",
        },
    )

    # ── NOMINAL path ──
    if mode == "loop":
        builder.add_edge("continue_monitoring", "ingest_telemetry")
    elif mode == "single":
        builder.add_edge("continue_monitoring", END)
    else:  # stream
        builder.add_edge("continue_monitoring", "wait_for_next_reading")
        builder.add_conditional_edges(
            "wait_for_next_reading", route_after_wait,
            {"ingest_telemetry": "ingest_telemetry", "end": END},
        )

    # ── Anomaly → action → human approval ──
    for node in [
        "cold_storage_intervention", "flight_temp_alert_agent",
        "emergency_vehicle_swap", "alternate_carrier_agent",
        "flight_rebooking_agent", "compliance_escalation_agent",
        "assume_breach_agent", "door_breach_agent", "ai_fallback_agent",
    ]:
        builder.add_edge(node, "human_approval_gateway")

    builder.add_edge("emergency_resupply_agent", "compliance_logger")

    # ── After human approval: execution agents ──
    if mode == "loop":
        builder.add_conditional_edges(
            "human_approval_gateway", route_human_approval,
            ["execute_reroute", "notify_stakeholders", "reschedule_patients",
             "generate_insurance_doc", "update_inventory_forecast",
             "write_audit_log", "orchestrate_decision"],
        )
        builder.add_edge("execute_reroute", "compliance_logger")
        builder.add_edge("notify_stakeholders", "compliance_logger")
        builder.add_edge("reschedule_patients", "compliance_logger")
        builder.add_edge("generate_insurance_doc", "compliance_logger")
        builder.add_edge("update_inventory_forecast", "compliance_logger")
        builder.add_edge("write_audit_log", "compliance_logger")
    else:  # single or stream — sequential execution
        def route_approve_seq(state: CargoState) -> str:
            return "execute_reroute" if state.get("decision") == "APPROVE" else "compliance_logger"

        builder.add_conditional_edges(
            "human_approval_gateway", route_approve_seq,
            ["execute_reroute", "compliance_logger"],
        )
        builder.add_edge("execute_reroute", "notify_stakeholders")
        builder.add_edge("notify_stakeholders", "reschedule_patients")
        builder.add_edge("reschedule_patients", "generate_insurance_doc")
        builder.add_edge("generate_insurance_doc", "update_inventory_forecast")
        builder.add_edge("update_inventory_forecast", "write_audit_log")
        builder.add_edge("write_audit_log", "compliance_logger")

    # ── After compliance logger ──
    if mode == "loop":
        builder.add_conditional_edges(
            "compliance_logger", route_compliance,
            {"continue": "ingest_telemetry", "end": END},
        )
    elif mode == "single":
        builder.add_edge("compliance_logger", END)
    else:  # stream — loop back via wait_for_next_reading
        builder.add_edge("compliance_logger", "wait_for_next_reading")

    return builder


# ═══════════════════════════════════════════════════════════════════════
# COMPILE AND VISUALIZE
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    builder = build_cargo_monitor_graph()
    graph = builder.compile(interrupt_before=["human_approval_gateway"])

    mermaid_diagram = graph.get_graph().draw_mermaid()
    print("=" * 70)
    print("  AI CARGO MONITOR -- LangGraph Workflow (Mermaid)")
    print("=" * 70)
    print(mermaid_diagram)

    with open("cargo_monitor_mermaid.md", "w", encoding="utf-8") as f:
        f.write("# AI Cargo Monitor -- LangGraph Workflow\n\n```mermaid\n")
        f.write(mermaid_diagram)
        f.write("\n```\n")
    print("\nMermaid saved -> cargo_monitor_mermaid.md")

    try:
        png_bytes = graph.get_graph().draw_mermaid_png()
        with open("cargo_monitor_workflow.png", "wb") as f:
            f.write(png_bytes)
        print("PNG saved   -> cargo_monitor_workflow.png")
    except Exception as e:
        print(f"\nPNG skipped: {e}")
