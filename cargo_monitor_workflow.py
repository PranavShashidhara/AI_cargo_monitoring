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
from datetime import datetime, timezone, timedelta
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt

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

    return {
        "reading_id": state.get("reading_id", str(uuid.uuid4())[:8]),
        "ingested_at": now,
        "unit_valid": unit_valid,
        "normalized_temp_c": round(temp, 2),
        "alert_class": alert,
        "gps_stall_detected": gps_stall,
        "sensor_silence": sensor_silent,
    }


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

    if door:
        anomaly_type = "DOOR_BREACH"
        severity = "CRITICAL"
    elif battery > 0 and battery < 20.0:
        anomaly_type = "SENSOR_SILENCE"
        severity = "HIGH"
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
    gps_stall = state.get("gps_stall_detected", False)
    speed = state.get("speed_kmh", 0.0)
    leg_mode = state.get("leg_mode", "")
    lat = state.get("lat", 0.0)
    lng = state.get("lng", 0.0)

    stall = gps_stall and leg_mode == "truck" and speed == 0.0
    duration = 30 if stall else 0

    return {
        "stall_confirmed": stall,
        "stall_duration_mins": duration,
        "stall_location": f"{lat:.4f},{lng:.4f}" if stall else "",
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
    status = state.get("customs_status", "NOT_APPLICABLE")

    if status.startswith("HOLD"):
        reason = status.replace("HOLD_", "").replace("_", " ").title()
        return {
            "hold_reason": reason,
            "hold_duration_est": 6.0,
            "docs_valid": False,
        }

    if status == "CLEARED":
        return {"hold_reason": "", "hold_duration_est": 0.0, "docs_valid": True}

    return {"hold_reason": "", "hold_duration_est": 0.0, "docs_valid": True}


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
    action_window = state.get("action_window_hrs", 48.0)
    accumulated = state.get("total_accumulated_delay_hrs", 0.0)
    spoilage = state.get("spoilage_prob", 0.0)
    anomaly = state.get("anomaly_type", "NOMINAL")
    delay = state.get("delay_hrs", 0.0)

    accumulated += delay
    budget = action_window - accumulated

    viable = budget > 0 and spoilage < 0.90

    ACTION_MAP = {
        "TEMP_BREACH":    ("Divert to nearest cold storage", 2.0),
        "REEFER_FAILURE": ("Emergency vehicle swap", 1.5),
        "TRUCK_STALL":    ("Dispatch alternate carrier", 1.0),
        "FLIGHT_CANCEL":  ("Rebook next available cargo flight", 4.0),
        "CUSTOMS_HOLD":   ("Escalate to customs broker", 0.5),
        "SENSOR_SILENCE": ("Assume breach protocol", 0.5),
        "DOOR_BREACH":    ("Integrity inspection at next stop", 1.0),
    }

    action, cost = ACTION_MAP.get(anomaly, ("Continue monitoring", 0.0))
    if not viable:
        action = "Declare compromised -- trigger emergency resupply"
        cost = 0.0

    return {
        "total_accumulated_delay_hrs": round(accumulated, 2),
        "delay_budget_hrs": round(budget, 2),
        "shipment_viable": viable,
        "recommended_action": action,
        "action_time_cost_hrs": cost,
    }


# ═══════════════════════════════════════════════════════════════════════
# ⑥ DECISION ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════

def orchestrate_decision(state: CargoState) -> dict:
    anomaly = state.get("anomaly_type", "NOMINAL")
    viable = state.get("shipment_viable", True)
    spoilage = state.get("spoilage_prob", 0.0)
    stall = state.get("stall_confirmed", False)
    cancelled = state.get("cancelled", False)
    hold = state.get("hold_reason", "")
    silence = state.get("sensor_silence", False)
    door = state.get("door_open", False)

    cascade = anomaly != "NOMINAL" or not viable

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
    }


def route_orchestrator(state: CargoState) -> str:
    if not state.get("shipment_viable", True):
        return "NOT_VIABLE"
    anomaly = state.get("anomaly_type", "NOMINAL")
    valid = {
        "TEMP_BREACH", "REEFER_FAILURE", "TRUCK_STALL",
        "FLIGHT_CANCEL", "CUSTOMS_HOLD", "SENSOR_SILENCE",
        "DOOR_BREACH",
    }
    if anomaly in valid:
        return anomaly
    if anomaly != "NOMINAL":
        # Any unrecognised non-nominal anomaly goes to the AI fallback agent
        return "UNKNOWN"
    return "NOMINAL"


# ═══════════════════════════════════════════════════════════════════════
# ⑦ ACTION AGENTS
# ═══════════════════════════════════════════════════════════════════════

def continue_monitoring(state: CargoState) -> dict:
    return {"recommended_action": "Continue monitoring -- no anomaly detected"}


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

    return {
        "cold_storage_location": f"{facility['name']} ({facility['id']}) - {dist_km}km away",
        "cold_storage_lat": facility["lat"],
        "cold_storage_lng": facility["lng"],
        "detour_time_hrs": detour_hrs,
        "updated_eta": new_eta.isoformat(),
        "hub_storage_needed": "hub" not in facility.get("type", ""),
        "time_impact_hrs": detour_hrs,
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
    hold_reason = state.get("hold_reason", "Unknown")
    docs_valid = state.get("docs_valid", True)

    broker = CUSTOMS_BROKERS[0]
    missing = []
    if not docs_valid:
        if "fda" in hold_reason.lower():
            missing = ["FDA Form 2877", "Certificate of Analysis", "Temperature logger data"]
        else:
            missing = ["Cold chain certification", "Importer of Record documentation"]

    return {
        "escalation_contact": f"{broker['name']}: {broker['contact']} ({broker['email']})",
        "missing_docs_list": json.dumps(missing),
        "broker_notified": True,
        "time_impact_hrs": float(broker["response_hrs"]),
    }


def assume_breach_agent(state: CargoState) -> dict:
    battery = state.get("battery_pct", 100.0)
    temp = state.get("normalized_temp_c", state.get("temp_c", 5.0))

    cause = "Tracker battery death" if battery <= 0 else "Sensor silence > 5 min"

    return {
        "anomaly_type": "SENSOR_SILENCE",
        "severity": "CRITICAL",
        "risk_narrative": (
            f"ASSUMED BREACH: {cause}. Last known temp: {temp}C. "
            f"Battery: {battery}%. Treating as worst-case temperature excursion "
            f"per GDP 2013/C 343/01 Section 9.3 (monitoring gaps)."
        ),
        "spoilage_prob": 0.70,
    }


def door_breach_agent(state: CargoState) -> dict:
    temp = state.get("normalized_temp_c", state.get("temp_c", 5.0))
    ambient = state.get("ambient_temp_c", 30.0)
    leg = state.get("leg", "")

    return {
        "anomaly_type": "DOOR_BREACH",
        "severity": "CRITICAL",
        "risk_narrative": (
            f"CONTAINER BREACH: Door opened during {leg.replace('_', ' ')}. "
            f"Container temp: {temp}C, ambient: {ambient}C. "
            f"Any exposure compromises vaccine integrity per "
            f"WHO/PQS/E06/IN05.4. Immediate inspection required."
        ),
        "spoilage_prob": 0.60,
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
    }


def notify_stakeholders(state: CargoState) -> dict:
    sid = state.get("shipment_id", "N/A")
    eta = state.get("updated_eta", "TBD")
    narrative = state.get("risk_narrative", "")
    hospital = state.get("destination_hospital", "")
    anomaly = state.get("anomaly_type", "NOMINAL")

    notifications = []

    notifications.append(
        f"TO: {hospital} Ops Team | "
        f"Shipment {sid} status update: {anomaly.replace('_', ' ')}. "
        f"Revised ETA: {eta}. Action: {state.get('recommended_action', 'N/A')}"
    )

    if state.get("broker_notified"):
        notifications.append(
            f"TO: Customs Broker | "
            f"Shipment {sid} customs escalation. {state.get('escalation_contact', '')}"
        )

    if state.get("new_flight_id"):
        notifications.append(
            f"TO: Airline Ops | "
            f"Shipment {sid} rebooked to flight {state.get('new_flight_id', '')} "
            f"departing {state.get('new_departure_time', '')}"
        )

    backup = BACKUP_HOSPITALS.get(hospital)
    if backup:
        notifications.append(
            f"TO: {backup['backup']} (BACKUP) | "
            f"Standby for potential reroute of shipment {sid}"
        )

    return {
        "notifications_sent": json.dumps(notifications),
        "backup_facility": backup["backup"] if backup else "",
    }


def reschedule_patients(state: CargoState) -> dict:
    hospital = state.get("destination_hospital", "Hospital")
    sid = state.get("shipment_id", "N/A")

    # time_impact_hrs = delay caused by the action agent (detour, vehicle swap, etc.)
    # delay_hrs = flight-specific delay from monitor_flight_status
    # Use whichever is greater; ignore accumulated total (not yet updated at this point)
    action_delay = state.get("time_impact_hrs", 0.0) or 0.0
    flight_delay = state.get("delay_hrs", 0.0) or 0.0
    delay = max(action_delay, flight_delay)

    # Format ETA for human readability
    raw_eta = state.get("updated_eta", "")
    if raw_eta:
        try:
            from datetime import timezone as _tz
            eta_dt = datetime.fromisoformat(raw_eta).astimezone(timezone.utc)
            eta_display = eta_dt.strftime("%d %b %Y, %H:%M UTC")
        except Exception:
            eta_display = raw_eta
    else:
        eta_display = "to be confirmed"

    count = max(0, int(delay * 3))
    if count == 0:
        return {"rescheduled_count": 0, "notifications_sent": json.dumps([])}

    return {
        "rescheduled_count": count,
        "notifications_sent": json.dumps([
            f"TO: {hospital} Scheduling Desk | "
            f"Vaccine shipment {sid} has been delayed by ~{delay:.1f} hrs due to a cold-chain intervention. "
            f"Approximately {count} patient appointment(s) may need to be rescheduled. "
            f"Revised delivery ETA: {eta_display}."
        ]),
    }


def generate_insurance_doc(state: CargoState) -> dict:
    sid = state.get("shipment_id", "N/A")
    anomaly = state.get("anomaly_type", "UNKNOWN")
    spoilage = state.get("spoilage_prob", 0.0)
    delay = state.get("total_accumulated_delay_hrs", 0.0)
    orig_days = state.get("insurance_days", 4)
    vtype = state.get("vaccine_type", "standard_flu")

    extra_days = max(1, int(delay / 24) + 1)
    new_end = datetime.now(timezone.utc) + timedelta(days=orig_days + extra_days)

    exposure = round(spoilage * 50000, 2)

    claim = {
        "claim_type": "Cold Chain Excursion",
        "shipment_id": sid,
        "vaccine_type": vtype,
        "anomaly_type": anomaly,
        "spoilage_probability": f"{spoilage:.1%}",
        "estimated_financial_exposure_usd": exposure,
        "original_coverage_days": orig_days,
        "extended_coverage_days": orig_days + extra_days,
        "delay_hours": delay,
        "filed_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "insurance_doc_url": f"outputs/insurance_claim_{sid}.json",
        "new_coverage_end_date": new_end.strftime("%Y-%m-%d"),
    }


def update_inventory_forecast(state: CargoState) -> dict:
    hospital = state.get("destination_hospital", "Hospital")
    vtype = state.get("vaccine_type", "standard_flu")
    delay = state.get("total_accumulated_delay_hrs", 0.0)
    viable = state.get("shipment_viable", True)

    shortfall = not viable or delay > 24.0

    return {
        "inventory_forecast_updated": True,
        "risk_narrative": (
            state.get("risk_narrative", "") +
            f" | INVENTORY: {hospital} forecast updated for {vtype}. "
            + (f"Delay {delay:.1f}hrs -- SHORTFALL RISK, backup allocation triggered."
               if shortfall else
               f"Delay {delay:.1f}hrs -- within buffer, no shortfall expected.")
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

    return {
        "audit_log_id": log_id,
        "compliance_flags": json.dumps([
            audit["compliance_status"],
            "GDP_LOGGED",
            "HUMAN_APPROVAL_RECORDED" if state.get("approved_by") else "PENDING_APPROVAL",
        ]),
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
    builder.add_node("orchestrate_decision", orchestrate_decision)
    builder.add_node("continue_monitoring", continue_monitoring)
    builder.add_node("cold_storage_intervention", cold_storage_intervention)
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
    builder.add_edge("check_viability_budget", "orchestrate_decision")

    builder.add_conditional_edges(
        "orchestrate_decision", route_orchestrator,
        {
            "NOMINAL":        "continue_monitoring",
            "TEMP_BREACH":    "cold_storage_intervention",
            "REEFER_FAILURE": "emergency_vehicle_swap",
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
        "cold_storage_intervention", "emergency_vehicle_swap",
        "alternate_carrier_agent", "flight_rebooking_agent",
        "compliance_escalation_agent", "assume_breach_agent",
        "door_breach_agent", "ai_fallback_agent",
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
