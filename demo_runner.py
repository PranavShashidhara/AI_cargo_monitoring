"""
AI Cargo Monitor -- Live Demo Runner
=====================================
Feeds real telemetry readings through the full LangGraph pipeline and
prints a step-by-step visualization of every agent's work.

Run:
    python demo_runner.py                        # all 3 scenarios
    python demo_runner.py --scenario temp_breach # just one
    python demo_runner.py --live SHP-003         # stream from telemetry.json

Requires: pip install langgraph
"""

import json
import sys
import os
import time
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from cargo_monitor_workflow import (
    ingest_telemetry,
    detect_anomaly,
    detect_truck_stall,
    monitor_flight_status,
    monitor_customs,
    score_risk,
    check_viability_budget,
    orchestrate_decision,
    route_orchestrator,
    continue_monitoring,
    cold_storage_intervention,
    emergency_vehicle_swap,
    alternate_carrier_agent,
    flight_rebooking_agent,
    compliance_escalation_agent,
    assume_breach_agent,
    door_breach_agent,
    emergency_resupply_agent,
    execute_reroute,
    notify_stakeholders,
    reschedule_patients,
    generate_insurance_doc,
    update_inventory_forecast,
    write_audit_log,
    compliance_logger,
    route_compliance,
    ai_fallback_agent,
)

# ═══════════════════════════════════════════════════════════════════════
# TERMINAL COLORS (ANSI escape codes)
# ═══════════════════════════════════════════════════════════════════════

os.system("")  # enable ANSI on Windows

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"
BG_RED  = "\033[41m"
BG_GREEN = "\033[42m"
BG_BLUE = "\033[44m"


def header(text: str, color=CYAN):
    width = 72
    print(f"\n{color}{BOLD}{'=' * width}")
    print(f"  {text}")
    print(f"{'=' * width}{RESET}")


def step(number: int, name: str, desc: str):
    print(f"\n  {BLUE}{BOLD}[Step {number}]{RESET} {BOLD}{name}{RESET}")
    print(f"  {DIM}{desc}{RESET}")


def result_line(key: str, value, color=WHITE):
    if isinstance(value, str) and len(value) > 80:
        value = value[:77] + "..."
    print(f"    {color}{key:30s}{RESET} = {value}")


def alert_box(label: str, message: str, color=RED):
    print(f"\n  {color}{BOLD}  [{label}] {message}  {RESET}")


def success_box(message: str):
    alert_box("OK", message, GREEN)


def warn_box(message: str):
    alert_box("!!", message, YELLOW)


def critical_box(message: str):
    alert_box("CRITICAL", message, RED)


def divider():
    print(f"  {DIM}{'- ' * 36}{RESET}")


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

SCENARIOS = {
    "temp_breach": {
        "title": "Temperature Breach (SHP-003)",
        "description": "Vaccine shipment from Mumbai hits 11.5C during flight -- reefer malfunction suspected",
        "reading": {
            "shipment_id": "SHP-003", "timestamp": "2026-04-05T20:00:00+00:00",
            "elapsed_hrs": 14.0, "leg": "flight_to_dest", "leg_mode": "flight",
            "temp_c": 11.5, "humidity_pct": 55.0, "shock_g": 0.2, "door_open": False,
            "lat": 35.0, "lng": -30.0, "speed_kmh": 850.0, "heading": 280.0,
            "carrier_id": "EK_CARGO_01", "battery_pct": 78.0,
            "flight_status": "IN_AIR", "customs_status": "NOT_APPLICABLE",
            "eta_delta_hrs": 0.0, "ambient_temp_c": -40.0,
            "vaccine_type": "standard_flu", "viability_window_hrs": 96.0,
            "pack_time": "2026-04-05T06:00:00+00:00",
            "destination_hospital": "NYC General Hospital",
            "origin_airport": "BOM", "dest_airport": "JFK", "transit_hub": "DXB",
            "insurance_days": 4,
            "temp_range_min": 2.0, "temp_range_max": 8.0,
        },
    },
    "customs_hold": {
        "title": "Customs Hold at JFK (SHP-007)",
        "description": "FDA documentation issue causes shipment hold at JFK customs",
        "reading": {
            "shipment_id": "SHP-007", "timestamp": "2026-04-06T16:00:00+00:00",
            "elapsed_hrs": 28.0, "leg": "dest_airport_customs", "leg_mode": "airport",
            "temp_c": 5.2, "humidity_pct": 40.0, "shock_g": 0.0, "door_open": False,
            "lat": 40.641, "lng": -73.778, "speed_kmh": 0.0, "heading": 0.0,
            "carrier_id": "DHL_COLD", "battery_pct": 65.0,
            "flight_status": "NOT_APPLICABLE", "customs_status": "HOLD_FDA_DOCS",
            "eta_delta_hrs": 0.0, "ambient_temp_c": 22.0,
            "vaccine_type": "standard_flu", "viability_window_hrs": 96.0,
            "pack_time": "2026-04-05T12:00:00+00:00",
            "destination_hospital": "NYC General Hospital",
            "origin_airport": "BOM", "dest_airport": "JFK", "transit_hub": "DXB",
            "insurance_days": 4,
            "temp_range_min": 2.0, "temp_range_max": 8.0,
        },
    },
    "earthquake_disruption": {
        "title": "Earthquake Disruption at DXB Transit Hub (SHP-005)",
        "description": "Earthquake reported near Dubai hub — automated rules can't classify risk, Claude AI decides",
        "reading": {
            "shipment_id": "SHP-005", "timestamp": "2026-04-07T08:00:00+00:00",
            "elapsed_hrs": 18.0, "leg": "transit_hub_hold", "leg_mode": "airport",
            "temp_c": 6.8, "humidity_pct": 82.0, "shock_g": 0.4, "door_open": False,
            "lat": 25.2532, "lng": 55.3657, "speed_kmh": 0.0, "heading": 0.0,
            "carrier_id": "EK_CARGO_01", "battery_pct": 55.0,
            "flight_status": "DELAYED", "customs_status": "PENDING",
            "eta_delta_hrs": 3.5, "ambient_temp_c": 38.0,
            "route_alerts": "EARTHQUAKE 4.2 magnitude reported near DXB terminal 3",
            "airport_conditions": "Partial terminal evacuation, cold storage access restricted",
            "vaccine_type": "standard_flu", "viability_window_hrs": 96.0,
            "pack_time": "2026-04-06T14:00:00+00:00",
            "destination_hospital": "NYC General Hospital",
            "origin_airport": "BOM", "dest_airport": "JFK", "transit_hub": "DXB",
            "insurance_days": 4,
            "temp_range_min": 2.0, "temp_range_max": 8.0,
        },
    },
    "flight_cancel": {
        "title": "Flight Cancelled at BOM (SHP-008)",
        "description": "Cargo flight BOM->DXB cancelled, vaccine sitting on tarmac",
        "reading": {
            "shipment_id": "SHP-008", "timestamp": "2026-04-05T10:00:00+00:00",
            "elapsed_hrs": 6.0, "leg": "origin_airport_wait", "leg_mode": "airport",
            "temp_c": 5.8, "humidity_pct": 50.0, "shock_g": 0.0, "door_open": False,
            "lat": 19.0896, "lng": 72.8656, "speed_kmh": 0.0, "heading": 0.0,
            "carrier_id": "EK_CARGO_01", "battery_pct": 88.0,
            "flight_status": "CANCELLED", "customs_status": "NOT_APPLICABLE",
            "eta_delta_hrs": 0.0, "ambient_temp_c": 34.0,
            "vaccine_type": "standard_flu", "viability_window_hrs": 96.0,
            "pack_time": "2026-04-05T04:00:00+00:00",
            "destination_hospital": "Mount Sinai",
            "origin_airport": "BOM", "dest_airport": "JFK", "transit_hub": "DXB",
            "insurance_days": 4,
            "temp_range_min": 2.0, "temp_range_max": 8.0,
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════
# PIPELINE EXECUTION
# ═══════════════════════════════════════════════════════════════════════

ACTION_ROUTER = {
    "NOMINAL":        ("continue_monitoring",         continue_monitoring),
    "TEMP_BREACH":    ("cold_storage_intervention",   cold_storage_intervention),
    "REEFER_FAILURE": ("emergency_vehicle_swap",      emergency_vehicle_swap),
    "TRUCK_STALL":    ("alternate_carrier_agent",     alternate_carrier_agent),
    "FLIGHT_CANCEL":  ("flight_rebooking_agent",      flight_rebooking_agent),
    "CUSTOMS_HOLD":   ("compliance_escalation_agent", compliance_escalation_agent),
    "SENSOR_SILENCE": ("assume_breach_agent",         assume_breach_agent),
    "DOOR_BREACH":    ("door_breach_agent",           door_breach_agent),
    "NOT_VIABLE":     ("emergency_resupply_agent",    emergency_resupply_agent),
    "UNKNOWN":        ("ai_fallback_agent",           ai_fallback_agent),
}


def run_scenario(scenario: dict, delay: float = 0.3):
    """Execute the full pipeline on a single reading with visual output."""

    title = scenario["title"]
    desc = scenario["description"]
    state = dict(scenario["reading"])

    header(f"SCENARIO: {title}", MAGENTA)
    print(f"  {desc}\n")
    print(f"  {DIM}Shipment : {state['shipment_id']}")
    print(f"  Vaccine : {state['vaccine_type']} ({state.get('temp_range_min')}C to {state.get('temp_range_max')}C)")
    print(f"  Leg     : {state['leg']} ({state['leg_mode']})")
    print(f"  Temp    : {state['temp_c']}C")
    print(f"  Position: {state['lat']}, {state['lng']}{RESET}")

    # ── Step 1: Ingest ──
    time.sleep(delay)
    step(1, "INGEST TELEMETRY", "Normalize raw sensor data, validate units, classify alert")
    r = ingest_telemetry(state)
    state.update(r)
    result_line("alert_class", state["alert_class"],
                RED if state["alert_class"] == "CRITICAL" else
                YELLOW if state["alert_class"] == "WATCH" else GREEN)
    result_line("normalized_temp_c", f"{state['normalized_temp_c']}C")
    result_line("gps_stall_detected", state["gps_stall_detected"],
                RED if state["gps_stall_detected"] else GREEN)
    result_line("sensor_silence", state["sensor_silence"],
                RED if state["sensor_silence"] else GREEN)

    # ── Step 2: Parallel Detection (fan-out) ──
    time.sleep(delay)
    step(2, "PARALLEL DETECTION", "4 agents run simultaneously: anomaly, truck stall, flight, customs")
    print(f"    {DIM}[fan-out: 4 parallel agents]{RESET}")

    r1 = detect_anomaly(state)
    r2 = detect_truck_stall(state)
    r3 = monitor_flight_status(state)
    r4 = monitor_customs(state)

    state.update(r1); state.update(r2); state.update(r3); state.update(r4)

    divider()
    atype = state.get("anomaly_type", "NOMINAL")
    acolor = RED if state.get("severity") == "CRITICAL" else YELLOW if state.get("severity") == "HIGH" else GREEN
    result_line("anomaly_type", atype, acolor)
    result_line("severity", state.get("severity", ""), acolor)
    result_line("stall_confirmed", state.get("stall_confirmed", False),
                RED if state.get("stall_confirmed") else GREEN)
    result_line("flight_event_type", state.get("flight_event_type", ""),
                RED if state.get("cancelled") else GREEN)
    result_line("customs hold_reason", state.get("hold_reason", "") or "(none)",
                RED if state.get("hold_reason") else GREEN)
    print(f"    {DIM}[fan-in: all 4 results merged]{RESET}")

    # ── Step 3: Risk Scoring ──
    time.sleep(delay)
    step(3, "RISK SCORING", "RAG over FDA/GDP/WHO regulations, calculate spoilage probability")
    r = score_risk(state)
    state.update(r)

    sp = state["spoilage_prob"]
    sp_color = RED if sp > 0.5 else YELLOW if sp > 0.2 else GREEN
    result_line("spoilage_prob", f"{sp:.1%}", sp_color)
    result_line("action_window_hrs", f"{state['action_window_hrs']} hrs")
    result_line("rag_citation", state["rag_citation"], CYAN)
    divider()
    print(f"    {DIM}Narrative:{RESET}")
    narrative = state["risk_narrative"]
    for i in range(0, len(narrative), 70):
        print(f"    {DIM}{narrative[i:i+70]}{RESET}")

    # ── Step 4: Viability Budget ──
    time.sleep(delay)
    step(4, "VIABILITY BUDGET CHECK", "Calculate remaining time budget, determine if shipment is viable")
    r = check_viability_budget(state)
    state.update(r)

    viable = state["shipment_viable"]
    result_line("delay_budget_hrs", f"{state['delay_budget_hrs']} hrs",
                GREEN if state["delay_budget_hrs"] > 10 else YELLOW if state["delay_budget_hrs"] > 0 else RED)
    result_line("shipment_viable", viable, GREEN if viable else RED)
    result_line("recommended_action", state["recommended_action"], CYAN)
    result_line("action_time_cost_hrs", f"{state['action_time_cost_hrs']} hrs")

    if not viable:
        critical_box(f"SHIPMENT NOT VIABLE -- delay budget exhausted ({state['delay_budget_hrs']} hrs)")

    # ── Step 5: Orchestrator ──
    time.sleep(delay)
    step(5, "DECISION ORCHESTRATOR", "Conditional routing based on failure type + viability")
    r = orchestrate_decision(state)
    state.update(r)

    route = route_orchestrator(state)
    result_line("cascade_triggered", state["cascade_triggered"],
                RED if state["cascade_triggered"] else GREEN)
    result_line("routed_to", route, MAGENTA)

    # ── Step 6: Action Agent ──
    time.sleep(delay)
    action_name, action_fn = ACTION_ROUTER.get(route, ("continue_monitoring", continue_monitoring))
    step(6, f"ACTION: {action_name}", f"Handling {route} scenario")

    if route == "NOMINAL":
        r = continue_monitoring(state)
        state.update(r)
        success_box("No anomaly detected -- looping back to ingest")
        print(f"\n  {GREEN}{BOLD}  Pipeline loops back to Step 1 for next reading  {RESET}")
        return state

    r = action_fn(state)
    state.update(r)
    for k, v in r.items():
        if v and v != "" and v != 0 and v is not False:
            result_line(k, v, CYAN)

    if route == "NOT_VIABLE":
        critical_box("Shipment declared COMPROMISED -- emergency resupply triggered")
        step(10, "COMPLIANCE LOGGER", "Final compliance snapshot, disposal required")
        r = compliance_logger(state)
        state.update(r)
        flags = json.loads(state.get("compliance_flags", "[]"))
        for f in flags:
            fc = RED if "COMPROMISED" in f or "DISPOSAL" in f else GREEN
            print(f"    {fc}  [{f}]{RESET}")
        print(f"\n  {RED}{BOLD}  Pipeline terminates -- shipment compromised  {RESET}")
        return state

    # ── Step 7: Human Approval ──
    time.sleep(delay)
    step(7, "HUMAN-IN-THE-LOOP APPROVAL", "Operator reviews risk and approves/rejects")
    print(f"\n    {YELLOW}{BOLD}--- INTERRUPT: Waiting for human approval ---{RESET}")
    print(f"    {DIM}(In production, Streamlit UI pauses here){RESET}\n")
    print(f"    {WHITE}Risk:     {state.get('risk_narrative', '')[:70]}...{RESET}")
    print(f"    {WHITE}Spoilage: {state.get('spoilage_prob', 0):.1%}{RESET}")
    print(f"    {WHITE}Action:   {state.get('recommended_action', '')}{RESET}")
    print(f"    {WHITE}Budget:   {state.get('delay_budget_hrs', 0)} hrs remaining{RESET}")
    print()

    state["decision"] = "APPROVE"
    state["approved_by"] = "demo_operator"
    state["approved_at"] = datetime.now(timezone.utc).isoformat()
    alert_box("APPROVED", f"Operator '{state['approved_by']}' approved at {state['approved_at'][:19]}", GREEN)

    # ── Step 8: Parallel Execution (fan-out) ──
    time.sleep(delay)
    step(8, "PARALLEL EXECUTION", "6 agents fire simultaneously on approval")
    print(f"    {DIM}[fan-out: 6 parallel execution agents]{RESET}")

    executors = [
        ("execute_reroute",          execute_reroute),
        ("notify_stakeholders",      notify_stakeholders),
        ("reschedule_patients",      reschedule_patients),
        ("generate_insurance_doc",   generate_insurance_doc),
        ("update_inventory_forecast", update_inventory_forecast),
        ("write_audit_log",          write_audit_log),
    ]

    for name, fn in executors:
        r = fn(state)
        state.update(r)
        key_vals = {k: v for k, v in r.items() if v and v != "" and v != 0 and v is not False}
        if key_vals:
            first_key = list(key_vals.keys())[0]
            print(f"    {GREEN}+{RESET} {name:35s} -> {first_key}={key_vals[first_key]}")

    print(f"    {DIM}[fan-in: all 6 results merged]{RESET}")

    # ── Step 9: Compliance Logger ──
    time.sleep(delay)
    step(9, "COMPLIANCE LOGGER", "Final compliance snapshot + GDP/FDA flags")
    r = compliance_logger(state)
    state.update(r)

    flags = json.loads(state.get("compliance_flags", "[]"))
    for f in flags:
        fc = RED if "COMPROMISED" in f else GREEN
        print(f"    {fc}  [{f}]{RESET}")

    route_end = route_compliance(state)
    if route_end == "continue":
        success_box("Shipment still viable -- pipeline loops back to Step 1")
    else:
        critical_box("Pipeline terminates -- shipment compromised")

    return state


# ═══════════════════════════════════════════════════════════════════════
# LIVE STREAM MODE
# ═══════════════════════════════════════════════════════════════════════

def run_live_stream(shipment_id: str, max_readings: int = 20):
    """Stream from telemetry.json and run each reading through the pipeline."""
    from data.stream_simulator import TelemetryStream

    stream = TelemetryStream(shipment_id=shipment_id, speed=0)
    meta = stream.get_shipment_metadata()

    if not meta:
        print(f"{RED}Shipment {shipment_id} not found in shipments.json{RESET}")
        return

    header(f"LIVE STREAM: {shipment_id}", BLUE)
    print(f"  Vaccine : {meta['vaccine_type']} ({meta['temp_range_min']}C to {meta['temp_range_max']}C)")
    print(f"  Route   : {meta['origin_airport']} -> {meta['transit_hub']} -> {meta['dest_airport']}")
    print(f"  Hospital: {meta['destination_hospital']}")
    print(f"  Readings: {len(stream)} available, processing up to {max_readings}\n")

    anomaly_count = 0

    for i, reading in enumerate(stream):
        if i >= max_readings:
            break

        reading.update({
            "vaccine_type": meta["vaccine_type"],
            "viability_window_hrs": meta.get("viability_window_hrs", 96.0),
            "pack_time": meta.get("pack_time", reading["timestamp"]),
            "destination_hospital": meta.get("destination_hospital", ""),
            "origin_airport": meta.get("origin_airport", "BOM"),
            "dest_airport": meta.get("dest_airport", "JFK"),
            "transit_hub": meta.get("transit_hub", "DXB"),
            "temp_range_min": meta.get("temp_range_min", 2.0),
            "temp_range_max": meta.get("temp_range_max", 8.0),
            "insurance_days": 4,
        })

        state = dict(reading)

        r = ingest_telemetry(state); state.update(r)
        r1 = detect_anomaly(state); state.update(r1)
        r2 = detect_truck_stall(state); state.update(r2)
        r3 = monitor_flight_status(state); state.update(r3)
        r4 = monitor_customs(state); state.update(r4)

        alert = state["alert_class"]
        anomaly = state.get("anomaly_type", "NOMINAL")

        if alert == "NOMINAL" and anomaly == "NOMINAL":
            ac = GREEN
            marker = "."
        elif alert == "WATCH" or anomaly not in ("NOMINAL", ""):
            ac = YELLOW
            marker = "!"
            anomaly_count += 1
        else:
            ac = RED
            marker = "X"
            anomaly_count += 1

        leg_short = reading.get("leg", "")[:20]
        print(
            f"  {ac}{marker}{RESET} "
            f"{DIM}[{reading['timestamp'][:19]}]{RESET} "
            f"{leg_short:20s} "
            f"temp={reading['temp_c']:6.2f}C "
            f"alert={ac}{alert:8s}{RESET} "
            f"anomaly={ac}{anomaly}{RESET}"
        )

        if anomaly not in ("NOMINAL", ""):
            r = score_risk(state); state.update(r)
            r = check_viability_budget(state); state.update(r)
            print(
                f"    {YELLOW}^ spoilage={state['spoilage_prob']:.1%} "
                f"budget={state['delay_budget_hrs']:.1f}hrs "
                f"viable={state['shipment_viable']} "
                f"action=\"{state['recommended_action']}\"{RESET}"
            )

    print(f"\n  {BOLD}Summary: {i+1} readings processed, {anomaly_count} anomalies detected{RESET}")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="AI Cargo Monitor -- Demo Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo_runner.py                          Run all 3 demo scenarios
  python demo_runner.py --scenario temp_breach   Run one scenario
  python demo_runner.py --live SHP-003           Stream from telemetry.json
  python demo_runner.py --live SHP-003 --max 50  Stream up to 50 readings
        """)
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()),
                        help="Run a specific scenario")
    parser.add_argument("--live", metavar="SHIPMENT_ID",
                        help="Stream live telemetry for a shipment (e.g. SHP-003)")
    parser.add_argument("--max", type=int, default=20,
                        help="Max readings to process in --live mode (default: 20)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Delay between steps in seconds (default: 0.3)")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}")
    print(r"     _    ___    ____                         __  __             _ _            ")
    print(r"    / \  |_ _|  / ___|__ _ _ __ __ _  ___   |  \/  | ___  _ __ (_) |_ ___  _ __")
    print(r"   / _ \  | |  | |   / _` | '__/ _` |/ _ \  | |\/| |/ _ \| '_ \| | __/ _ \| '__|")
    print(r"  / ___ \ | |  | |__| (_| | | | (_| | (_) | | |  | | (_) | | | | | || (_) | |  ")
    print(r" /_/   \_\___|  \____\__,_|_|  \__, |\___/  |_|  |_|\___/|_| |_|_|\__\___/|_|  ")
    print(r"                               |___/                                            ")
    print(f"{RESET}")
    print(f"  {DIM}Pharma Cold-Chain Monitoring -- LangGraph Multi-Agent System{RESET}\n")

    if args.live:
        run_live_stream(args.live, max_readings=args.max)
    elif args.scenario:
        run_scenario(SCENARIOS[args.scenario], delay=args.delay)
    else:
        for name in SCENARIOS:
            run_scenario(SCENARIOS[name], delay=args.delay)
            print(f"\n{'=' * 72}\n")

    print(f"\n  {DIM}Demo complete.{RESET}\n")


if __name__ == "__main__":
    main()
