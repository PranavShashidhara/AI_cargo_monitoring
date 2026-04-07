"""
Tests for all 24 LangGraph node functions.
Run:  pytest tests/test_agents.py -v
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
)

# ═══════════════════════════════════════════════════════════════════════
# FIXTURES: reusable state dicts for each scenario
# ═══════════════════════════════════════════════════════════════════════

NORMAL_READING = {
    "shipment_id": "SHP-001",
    "timestamp": "2026-04-05T14:00:00+00:00",
    "elapsed_hrs": 2.0,
    "leg": "warehouse_to_airport",
    "leg_mode": "truck",
    "temp_c": 5.0,
    "humidity_pct": 45.0,
    "shock_g": 0.1,
    "door_open": False,
    "lat": 19.08,
    "lng": 72.87,
    "speed_kmh": 55.0,
    "battery_pct": 92.0,
    "flight_status": "NOT_APPLICABLE",
    "customs_status": "NOT_APPLICABLE",
    "eta_delta_hrs": 0.0,
    "ambient_temp_c": 32.0,
    "vaccine_type": "standard_flu",
    "viability_window_hrs": 96.0,
    "pack_time": "2026-04-05T10:00:00+00:00",
    "destination_hospital": "NYC General Hospital",
    "carrier_id": "DHL_COLD",
    "origin_airport": "BOM",
    "dest_airport": "JFK",
    "transit_hub": "DXB",
    "insurance_days": 4,
}

TEMP_BREACH_READING = {**NORMAL_READING,
    "shipment_id": "SHP-003",
    "temp_c": 11.5,
    "elapsed_hrs": 14.0,
    "leg": "flight_to_dest",
    "leg_mode": "flight",
    "speed_kmh": 850.0,
}

TRUCK_STALL_READING = {**NORMAL_READING,
    "shipment_id": "SHP-005",
    "speed_kmh": 0.0,
    "elapsed_hrs": 1.5,
    "leg": "warehouse_to_airport",
    "leg_mode": "truck",
}

CUSTOMS_HOLD_READING = {**NORMAL_READING,
    "shipment_id": "SHP-007",
    "customs_status": "HOLD_FDA_DOCS",
    "elapsed_hrs": 28.0,
    "leg": "dest_airport_customs",
    "leg_mode": "airport",
    "speed_kmh": 0.0,
}

FLIGHT_CANCEL_READING = {**NORMAL_READING,
    "shipment_id": "SHP-008",
    "flight_status": "CANCELLED",
    "elapsed_hrs": 6.0,
    "leg": "origin_airport_wait",
    "leg_mode": "airport",
    "speed_kmh": 0.0,
}

SENSOR_SILENCE_READING = {**NORMAL_READING,
    "shipment_id": "SHP-009",
    "battery_pct": 0.0,
    "elapsed_hrs": 18.0,
}

DOOR_BREACH_READING = {**NORMAL_READING,
    "shipment_id": "SHP-010",
    "door_open": True,
    "elapsed_hrs": 11.5,
    "leg": "transit_hub_wait",
    "leg_mode": "airport",
}


# ═══════════════════════════════════════════════════════════════════════
# GROUP A: INGEST
# ═══════════════════════════════════════════════════════════════════════

class TestIngestTelemetry:
    def test_normal_reading(self):
        r = ingest_telemetry(NORMAL_READING)
        assert r["alert_class"] == "NOMINAL"
        assert r["unit_valid"] is True
        assert r["gps_stall_detected"] is False
        assert r["sensor_silence"] is False
        assert r["normalized_temp_c"] == 5.0

    def test_temp_breach_flags_critical(self):
        r = ingest_telemetry(TEMP_BREACH_READING)
        assert r["alert_class"] == "CRITICAL"

    def test_truck_stall_detected(self):
        r = ingest_telemetry(TRUCK_STALL_READING)
        assert r["gps_stall_detected"] is True

    def test_sensor_silence_detected(self):
        r = ingest_telemetry(SENSOR_SILENCE_READING)
        assert r["sensor_silence"] is True
        assert r["alert_class"] == "CRITICAL"

    def test_watch_alert_near_boundary(self):
        near_boundary = {**NORMAL_READING, "temp_c": 7.5}
        r = ingest_telemetry(near_boundary)
        assert r["alert_class"] == "WATCH"


# ═══════════════════════════════════════════════════════════════════════
# GROUP B: DETECTION AGENTS (parallel)
# ═══════════════════════════════════════════════════════════════════════

class TestDetectAnomaly:
    def test_nominal(self):
        r = detect_anomaly(NORMAL_READING)
        assert r["anomaly_type"] == "NOMINAL"

    def test_temp_breach(self):
        r = detect_anomaly(TEMP_BREACH_READING)
        assert r["anomaly_type"] in ("TEMP_BREACH", "REEFER_FAILURE")
        assert r["severity"] in ("HIGH", "CRITICAL")

    def test_door_breach(self):
        r = detect_anomaly(DOOR_BREACH_READING)
        assert r["anomaly_type"] == "DOOR_BREACH"
        assert r["severity"] == "CRITICAL"

    def test_low_battery(self):
        low_bat = {**NORMAL_READING, "battery_pct": 15.0}
        r = detect_anomaly(low_bat)
        assert r["anomaly_type"] == "SENSOR_SILENCE"
        assert r["severity"] == "HIGH"

    def test_returns_detected_at(self):
        r = detect_anomaly(NORMAL_READING)
        assert "detected_at" in r and r["detected_at"]


class TestDetectTruckStall:
    def test_stall_confirmed(self):
        state = {**TRUCK_STALL_READING, "gps_stall_detected": True}
        r = detect_truck_stall(state)
        assert r["stall_confirmed"] is True
        assert r["stall_duration_mins"] == 30
        assert r["stall_location"] != ""

    def test_no_stall_when_moving(self):
        r = detect_truck_stall(NORMAL_READING)
        assert r["stall_confirmed"] is False

    def test_no_stall_at_airport(self):
        airport = {**NORMAL_READING, "speed_kmh": 0.0, "leg_mode": "airport",
                    "gps_stall_detected": False}
        r = detect_truck_stall(airport)
        assert r["stall_confirmed"] is False


class TestMonitorFlightStatus:
    def test_on_time(self):
        r = monitor_flight_status(NORMAL_READING)
        assert r["flight_event_type"] == "ON_TIME"
        assert r["cancelled"] is False

    def test_cancelled(self):
        r = monitor_flight_status(FLIGHT_CANCEL_READING)
        assert r["flight_event_type"] == "CANCELLED"
        assert r["cancelled"] is True
        assert r["delay_hrs"] == 8.0
        assert r["next_viable_flight"] != ""

    def test_delayed(self):
        delayed = {**NORMAL_READING, "flight_status": "IN_AIR", "eta_delta_hrs": 3.0}
        r = monitor_flight_status(delayed)
        assert r["flight_event_type"] == "DELAYED"
        assert r["delay_hrs"] >= 1.0


class TestMonitorCustoms:
    def test_cleared(self):
        cleared = {**NORMAL_READING, "customs_status": "CLEARED"}
        r = monitor_customs(cleared)
        assert r["docs_valid"] is True
        assert r["hold_reason"] == ""

    def test_hold(self):
        r = monitor_customs(CUSTOMS_HOLD_READING)
        assert r["docs_valid"] is False
        assert r["hold_reason"] != ""
        assert r["hold_duration_est"] > 0

    def test_not_applicable(self):
        r = monitor_customs(NORMAL_READING)
        assert r["docs_valid"] is True


# ═══════════════════════════════════════════════════════════════════════
# GROUP C: RISK + VIABILITY (sequential)
# ═══════════════════════════════════════════════════════════════════════

class TestScoreRisk:
    def test_nominal_zero_spoilage(self):
        state = {**NORMAL_READING, "anomaly_type": "NOMINAL", "severity": "LOW"}
        r = score_risk(state)
        assert r["spoilage_prob"] == 0.0
        assert r["action_window_hrs"] > 0

    def test_temp_breach_high_spoilage(self):
        state = {**TEMP_BREACH_READING, "anomaly_type": "TEMP_BREACH",
                 "severity": "CRITICAL", "normalized_temp_c": 11.5}
        r = score_risk(state)
        assert r["spoilage_prob"] > 0.3
        assert "risk_narrative" in r and len(r["risk_narrative"]) > 50
        assert r["rag_citation"] != ""

    def test_customs_hold_increases_spoilage(self):
        state = {**CUSTOMS_HOLD_READING, "anomaly_type": "NOMINAL",
                 "severity": "LOW", "hold_reason": "Fda Docs"}
        r = score_risk(state)
        assert r["spoilage_prob"] >= 0.2

    def test_narrative_contains_shipment_id(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH",
                 "severity": "HIGH", "normalized_temp_c": 9.0}
        r = score_risk(state)
        assert "SHP-001" in r["risk_narrative"]


class TestCheckViabilityBudget:
    def test_viable_shipment(self):
        state = {**NORMAL_READING, "action_window_hrs": 50.0,
                 "total_accumulated_delay_hrs": 2.0, "spoilage_prob": 0.1,
                 "anomaly_type": "NOMINAL", "delay_hrs": 0.0}
        r = check_viability_budget(state)
        assert r["shipment_viable"] is True
        assert r["delay_budget_hrs"] > 0

    def test_not_viable_when_budget_exhausted(self):
        state = {**NORMAL_READING, "action_window_hrs": 2.0,
                 "total_accumulated_delay_hrs": 5.0, "spoilage_prob": 0.5,
                 "anomaly_type": "TEMP_BREACH", "delay_hrs": 0.0}
        r = check_viability_budget(state)
        assert r["shipment_viable"] is False
        assert "emergency resupply" in r["recommended_action"].lower()

    def test_accumulates_delay(self):
        state = {**NORMAL_READING, "action_window_hrs": 50.0,
                 "total_accumulated_delay_hrs": 3.0, "spoilage_prob": 0.1,
                 "anomaly_type": "FLIGHT_CANCEL", "delay_hrs": 8.0}
        r = check_viability_budget(state)
        assert r["total_accumulated_delay_hrs"] == 11.0


# ═══════════════════════════════════════════════════════════════════════
# GROUP D: ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════

class TestOrchestrateDecision:
    def test_nominal_no_cascade(self):
        state = {**NORMAL_READING, "anomaly_type": "NOMINAL",
                 "shipment_viable": True, "spoilage_prob": 0.0}
        r = orchestrate_decision(state)
        assert r["cascade_triggered"] is False

    def test_temp_breach_triggers_cascade(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH",
                 "shipment_viable": True}
        r = orchestrate_decision(state)
        assert r["cascade_triggered"] is True

    def test_not_viable_overrides_anomaly(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH",
                 "shipment_viable": False}
        r = orchestrate_decision(state)
        assert r["anomaly_type"] == "NOT_VIABLE"


class TestRouteOrchestrator:
    def test_nominal_route(self):
        assert route_orchestrator({"anomaly_type": "NOMINAL", "shipment_viable": True}) == "NOMINAL"

    def test_temp_breach_route(self):
        assert route_orchestrator({"anomaly_type": "TEMP_BREACH", "shipment_viable": True}) == "TEMP_BREACH"

    def test_not_viable_route(self):
        assert route_orchestrator({"anomaly_type": "TEMP_BREACH", "shipment_viable": False}) == "NOT_VIABLE"

    def test_all_routes_valid(self):
        for a in ["TEMP_BREACH", "REEFER_FAILURE", "TRUCK_STALL", "FLIGHT_CANCEL",
                   "CUSTOMS_HOLD", "SENSOR_SILENCE", "DOOR_BREACH"]:
            assert route_orchestrator({"anomaly_type": a, "shipment_viable": True}) == a


# ═══════════════════════════════════════════════════════════════════════
# GROUP E: ACTION AGENTS (parallel)
# ═══════════════════════════════════════════════════════════════════════

class TestContinueMonitoring:
    def test_returns_action(self):
        r = continue_monitoring(NORMAL_READING)
        assert "recommended_action" in r


class TestColdStorageIntervention:
    def test_finds_facility(self):
        r = cold_storage_intervention(NORMAL_READING)
        assert r["cold_storage_location"] != ""
        assert r["detour_time_hrs"] > 0
        assert r["updated_eta"] != ""

    def test_near_jfk(self):
        jfk = {**NORMAL_READING, "lat": 40.64, "lng": -73.78}
        r = cold_storage_intervention(jfk)
        assert "JFK" in r["cold_storage_location"] or "Newark" in r["cold_storage_location"] or "Manhattan" in r["cold_storage_location"]


class TestEmergencyVehicleSwap:
    def test_finds_alternate(self):
        r = emergency_vehicle_swap(TRUCK_STALL_READING)
        assert r["new_vehicle_id"] != "NONE"
        assert r["transfer_eta_mins"] > 0
        assert r["new_carrier_contact"] != ""


class TestAlternateCarrierAgent:
    def test_finds_carrier(self):
        state = {**TRUCK_STALL_READING, "stall_duration_mins": 30}
        r = alternate_carrier_agent(state)
        assert r["alternate_carrier"] != "NONE"
        assert r["pickup_eta_mins"] > 0


class TestFlightRebookingAgent:
    def test_cancelled_gets_new_flight(self):
        state = {**FLIGHT_CANCEL_READING, "flight_event_type": "CANCELLED",
                 "action_window_hrs": 24.0}
        r = flight_rebooking_agent(state)
        assert r["new_flight_id"] != "NONE"
        assert r["hub_storage_needed"] is True

    def test_delayed_less_impact(self):
        state = {**NORMAL_READING, "flight_event_type": "DELAYED",
                 "action_window_hrs": 24.0, "leg": "origin_airport_wait"}
        r = flight_rebooking_agent(state)
        assert r["time_impact_hrs"] <= 8.0


class TestComplianceEscalation:
    def test_hold_triggers_escalation(self):
        state = {**CUSTOMS_HOLD_READING, "hold_reason": "Fda Docs",
                 "docs_valid": False}
        r = compliance_escalation_agent(state)
        assert r["broker_notified"] is True
        assert r["escalation_contact"] != ""
        assert "FDA" in json.loads(r["missing_docs_list"])[0]


class TestAssumeBreachAgent:
    def test_battery_death(self):
        state = {**SENSOR_SILENCE_READING, "sensor_silence": True,
                 "normalized_temp_c": 5.0}
        r = assume_breach_agent(state)
        assert r["severity"] == "CRITICAL"
        assert r["spoilage_prob"] == 0.70
        assert "battery" in r["risk_narrative"].lower()


class TestDoorBreachAgent:
    def test_door_open(self):
        state = {**DOOR_BREACH_READING, "normalized_temp_c": 5.0}
        r = door_breach_agent(state)
        assert r["anomaly_type"] == "DOOR_BREACH"
        assert r["severity"] == "CRITICAL"
        assert "WHO" in r["risk_narrative"]


class TestEmergencyResupply:
    def test_triggers_resupply(self):
        state = {**NORMAL_READING, "shipment_viable": False}
        r = emergency_resupply_agent(state)
        assert r["compromised_flag"] is True
        assert r["resupply_eta_hrs"] > 0
        assert r["nearest_who_facility"] != ""
        assert "COMPROMISED" in r["risk_narrative"]


# ═══════════════════════════════════════════════════════════════════════
# GROUP F: EXECUTION AGENTS (parallel)
# ═══════════════════════════════════════════════════════════════════════

class TestExecuteReroute:
    def test_with_actions(self):
        state = {**NORMAL_READING, "alternate_carrier": "FEDEX",
                 "cold_storage_location": "DXB Vault", "new_flight_id": "EK-510"}
        r = execute_reroute(state)
        assert r["reroute_confirmed"] is True
        assert "FLIGHT:EK-510" in r["new_route_polyline"]

    def test_no_action(self):
        r = execute_reroute(NORMAL_READING)
        assert r["reroute_confirmed"] is False


class TestNotifyStakeholders:
    def test_generates_notifications(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH",
                 "updated_eta": "2026-04-07T10:00:00",
                 "recommended_action": "Cold storage divert"}
        r = notify_stakeholders(state)
        notifs = json.loads(r["notifications_sent"])
        assert len(notifs) >= 1
        assert "NYC General Hospital" in notifs[0]

    def test_includes_backup(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH"}
        r = notify_stakeholders(state)
        assert r["backup_facility"] != ""


class TestReschedulePatients:
    def test_calculates_count(self):
        state = {**NORMAL_READING, "delay_hrs": 6.0,
                 "updated_eta": "2026-04-07T10:00:00"}
        r = reschedule_patients(state)
        assert r["rescheduled_count"] >= 1
        notifs = json.loads(r["notifications_sent"])
        assert "rescheduled" in notifs[0].lower()


class TestGenerateInsuranceDoc:
    def test_generates_claim(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH",
                 "spoilage_prob": 0.5, "total_accumulated_delay_hrs": 12.0}
        r = generate_insurance_doc(state)
        assert r["insurance_doc_url"].endswith(".json")
        assert r["new_coverage_end_date"] != ""


class TestUpdateInventoryForecast:
    def test_updates_forecast(self):
        state = {**NORMAL_READING, "total_accumulated_delay_hrs": 6.0,
                 "shipment_viable": True}
        r = update_inventory_forecast(state)
        assert r["inventory_forecast_updated"] is True

    def test_flags_shortfall(self):
        state = {**NORMAL_READING, "total_accumulated_delay_hrs": 30.0,
                 "shipment_viable": True}
        r = update_inventory_forecast(state)
        assert "SHORTFALL" in r["risk_narrative"]


class TestWriteAuditLog:
    def test_creates_log(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH",
                 "severity": "CRITICAL", "spoilage_prob": 0.5,
                 "recommended_action": "Cold storage", "approved_by": "operator_01",
                 "rag_citation": "FDA 21 CFR"}
        r = write_audit_log(state)
        assert r["audit_log_id"].startswith("AUDIT-")
        flags = json.loads(r["compliance_flags"])
        assert "HUMAN_APPROVAL_RECORDED" in flags


# ═══════════════════════════════════════════════════════════════════════
# GROUP G: COMPLIANCE LOGGER
# ═══════════════════════════════════════════════════════════════════════

class TestComplianceLogger:
    def test_normal_viable(self):
        state = {**NORMAL_READING, "anomaly_type": "NOMINAL",
                 "shipment_viable": True, "compromised_flag": False}
        r = compliance_logger(state)
        flags = json.loads(r["compliance_flags"])
        assert "GDP_COMPLIANT" in flags
        assert r["compromised_flag"] is False

    def test_compromised(self):
        state = {**NORMAL_READING, "anomaly_type": "TEMP_BREACH",
                 "shipment_viable": False, "compromised_flag": True,
                 "approved_by": "op1"}
        r = compliance_logger(state)
        flags = json.loads(r["compliance_flags"])
        assert "SHIPMENT_COMPROMISED" in flags
        assert "HUMAN_APPROVAL_VERIFIED" in flags


class TestRouteCompliance:
    def test_continue(self):
        assert route_compliance({"compromised_flag": False}) == "continue"

    def test_end(self):
        assert route_compliance({"compromised_flag": True}) == "end"


# ═══════════════════════════════════════════════════════════════════════
# INTEGRATION: Full pipeline (function-by-function, not graph)
# ═══════════════════════════════════════════════════════════════════════

class TestFullPipelineTempBreach:
    """Simulate SHP-003 temp breach end-to-end through all functions."""

    def test_end_to_end(self):
        state = dict(TEMP_BREACH_READING)

        r = ingest_telemetry(state)
        state.update(r)
        assert state["alert_class"] == "CRITICAL"

        r = detect_anomaly(state)
        state.update(r)
        assert state["anomaly_type"] in ("TEMP_BREACH", "REEFER_FAILURE")

        r = detect_truck_stall(state)
        state.update(r)
        assert state["stall_confirmed"] is False

        r = monitor_flight_status(state)
        state.update(r)

        r = monitor_customs(state)
        state.update(r)

        r = score_risk(state)
        state.update(r)
        assert state["spoilage_prob"] > 0

        r = check_viability_budget(state)
        state.update(r)

        r = orchestrate_decision(state)
        state.update(r)
        assert state["cascade_triggered"] is True

        route = route_orchestrator(state)
        assert route in ("TEMP_BREACH", "REEFER_FAILURE")

        r = cold_storage_intervention(state)
        state.update(r)
        assert state["cold_storage_location"] != ""

        state["decision"] = "APPROVE"
        state["approved_by"] = "test_operator"
        state["approved_at"] = "2026-04-06T10:00:00Z"

        r = execute_reroute(state)
        state.update(r)

        r = notify_stakeholders(state)
        state.update(r)

        r = reschedule_patients(state)
        state.update(r)

        r = generate_insurance_doc(state)
        state.update(r)

        r = update_inventory_forecast(state)
        state.update(r)

        r = write_audit_log(state)
        state.update(r)
        assert state["audit_log_id"].startswith("AUDIT-")

        r = compliance_logger(state)
        state.update(r)

        route = route_compliance(state)
        assert route in ("continue", "end")
