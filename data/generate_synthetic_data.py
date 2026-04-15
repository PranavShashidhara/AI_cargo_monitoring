"""
Synthetic Telemetry Generator
=============================
Generates realistic pharma cold-chain shipment data.
Each shipment is assigned a route profile — short domestic truck runs,
regional road trips, or full international air freight.
Telemetry is generated only for the actual route duration; the animation
stops exactly when the shipment reaches its destination.

Run:  python data/generate_synthetic_data.py
Output: data/shipments.json, data/telemetry.json
"""

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)

OUTPUT_DIR = Path(__file__).parent

# ═══════════════════════════════════════════════════════════════════════
# ROUTE PROFILES
# Each profile defines legs, origin/destination airports, hospitals, etc.
# ═══════════════════════════════════════════════════════════════════════

ROUTE_PROFILES = {

    # ── International: Mumbai -> Dubai -> New York ──────────────────────
    "international_bom_jfk": {
        "label": "Mumbai -> Dubai -> New York",
        "origin_airport": "BOM",
        "transit_hub": "DXB",
        "dest_airport": "JFK",
        "hospitals": [
            "NYC General Hospital", "Mount Sinai", "NYU Langone",
            "Columbia Presbyterian", "Bellevue Hospital",
        ],
        "legs": [
            {
                "leg": "warehouse_to_airport",
                "description": "Truck: Mumbai warehouse -> BOM airport",
                "start_coords": (19.0760, 72.8777),
                "end_coords":   (19.0896, 72.8656),
                "duration_hrs": 3.0,
                "mode": "truck",
            },
            {
                "leg": "origin_airport_wait",
                "description": "BOM: customs + loading",
                "start_coords": (19.0896, 72.8656),
                "end_coords":   (19.0896, 72.8656),
                "duration_hrs": 4.0,
                "mode": "airport",
            },
            {
                "leg": "flight_to_hub",
                "description": "Flight: BOM -> DXB",
                "start_coords": (19.0896, 72.8656),
                "end_coords":   (25.2532, 55.3657),
                "duration_hrs": 3.5,
                "mode": "flight",
            },
            {
                "leg": "transit_hub_wait",
                "description": "DXB hub: layover + cold storage",
                "start_coords": (25.2532, 55.3657),
                "end_coords":   (25.2532, 55.3657),
                "duration_hrs": 4.0,
                "mode": "airport",
            },
            {
                "leg": "flight_to_dest",
                "description": "Flight: DXB -> JFK",
                "start_coords": (25.2532, 55.3657),
                "end_coords":   (40.6413, -73.7781),
                "duration_hrs": 14.0,
                "mode": "flight",
            },
            {
                "leg": "dest_airport_customs",
                "description": "JFK: US customs + FDA inspection",
                "start_coords": (40.6413, -73.7781),
                "end_coords":   (40.6413, -73.7781),
                "duration_hrs": 5.0,
                "mode": "airport",
            },
            {
                "leg": "last_mile_delivery",
                "description": "Truck: JFK -> NYC Hospital",
                "start_coords": (40.6413, -73.7781),
                "end_coords":   (40.7128, -74.0060),
                "duration_hrs": 2.0,
                "mode": "truck",
            },
        ],
    },

    # ── Domestic short: Mumbai -> Ahmedabad (truck only, ~5 hrs) ─────────
    "domestic_mum_ahm": {
        "label": "Mumbai -> Ahmedabad",
        "origin_airport": None,
        "transit_hub": None,
        "dest_airport": None,
        "hospitals": [
            "Apollo Hospitals Ahmedabad", "Sterling Hospital Ahmedabad",
        ],
        "legs": [
            {
                "leg": "warehouse_to_truck",
                "description": "Truck: Mumbai cold storage -> city pickup",
                "start_coords": (19.0760, 72.8777),
                "end_coords":   (19.1200, 72.8500),
                "duration_hrs": 0.5,
                "mode": "truck",
            },
            {
                "leg": "road_transit",
                "description": "Truck: Mumbai -> Ahmedabad (NH48)",
                "start_coords": (19.1200, 72.8500),
                "end_coords":   (23.0225, 72.5714),
                "duration_hrs": 4.0,
                "mode": "truck",
            },
            {
                "leg": "last_mile_delivery",
                "description": "Truck: Ahmedabad city -> hospital",
                "start_coords": (23.0225, 72.5714),
                "end_coords":   (23.0300, 72.5600),
                "duration_hrs": 0.5,
                "mode": "truck",
            },
        ],
    },

    # ── Regional: Mumbai -> Delhi (truck + domestic flight, ~6 hrs) ──────
    "regional_mum_del": {
        "label": "Mumbai -> Delhi",
        "origin_airport": "BOM",
        "transit_hub": None,
        "dest_airport": "DEL",
        "hospitals": [
            "AIIMS Delhi", "Max Super Speciality Delhi",
        ],
        "legs": [
            {
                "leg": "warehouse_to_airport",
                "description": "Truck: Mumbai cold storage -> BOM airport",
                "start_coords": (19.0760, 72.8777),
                "end_coords":   (19.0896, 72.8656),
                "duration_hrs": 1.0,
                "mode": "truck",
            },
            {
                "leg": "origin_airport_wait",
                "description": "BOM: domestic check-in + loading",
                "start_coords": (19.0896, 72.8656),
                "end_coords":   (19.0896, 72.8656),
                "duration_hrs": 1.5,
                "mode": "airport",
            },
            {
                "leg": "flight_to_dest",
                "description": "Flight: BOM -> DEL",
                "start_coords": (19.0896, 72.8656),
                "end_coords":   (28.5562, 77.1000),
                "duration_hrs": 2.0,
                "mode": "flight",
            },
            {
                "leg": "dest_airport_customs",
                "description": "DEL: cargo collection",
                "start_coords": (28.5562, 77.1000),
                "end_coords":   (28.5562, 77.1000),
                "duration_hrs": 0.75,
                "mode": "airport",
            },
            {
                "leg": "last_mile_delivery",
                "description": "Truck: DEL airport -> hospital",
                "start_coords": (28.5562, 77.1000),
                "end_coords":   (28.6139, 77.2090),
                "duration_hrs": 0.75,
                "mode": "truck",
            },
        ],
    },
}

# Pre-compute total duration per profile
for _p in ROUTE_PROFILES.values():
    _p["total_hrs"] = sum(l["duration_hrs"] for l in _p["legs"])


VACCINE_PROFILES = [
    {"type": "standard_flu", "temp_min": 2.0, "temp_max": 8.0,   "viability_hrs": 96},
    {"type": "mRNA_pfizer",  "temp_min": -80.0, "temp_max": -60.0, "viability_hrs": 72},
    {"type": "standard_mmr", "temp_min": 2.0, "temp_max": 8.0,   "viability_hrs": 96},
]

CARRIERS = ["DHL_COLD", "FEDEX_PHARMA", "UPS_TEMP", "KUEHNE_NAGEL", "CEVA_LOGISTICS"]

# Assign a route profile to each shipment ID
SHIPMENT_ROUTES = {
    "SHP-001": "international_bom_jfk",
    "SHP-002": "international_bom_jfk",
    "SHP-003": "international_bom_jfk",
    "SHP-004": "international_bom_jfk",
    "SHP-005": "international_bom_jfk",
    "SHP-006": "domestic_mum_ahm",
    "SHP-007": "international_bom_jfk",
    "SHP-008": "international_bom_jfk",
    "SHP-009": "regional_mum_del",
    "SHP-010": "international_bom_jfk",
}

# ═══════════════════════════════════════════════════════════════════════
# ANOMALY INJECTION DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

ANOMALY_SCENARIOS = {
    # ── SHP-003: ALL failure types in one journey (demo shipment) ──────────
    # Covers every workflow node so operators can see the full system in action.
    # international BOM->DXB->JFK (35.5 hrs, 30-min readings)
    #
    # Leg timing reference:
    #   0.0– 3.0 h  warehouse_to_airport  (truck)
    #   3.0– 7.0 h  origin_airport_wait   (airport)
    #   7.0–10.5 h  flight_to_hub         (flight)
    #  10.5–14.5 h  transit_hub_wait      (airport)
    #  14.5–28.5 h  flight_to_dest        (flight)
    #  28.5–31.5 h  dest_airport_customs  (airport)
    #  31.5–35.5 h  last_mile_delivery    (truck)
    "SHP-003": [
        {
            "type": "TEMP_BREACH",
            "description": "Reefer temp drifts high on the warehouse truck leg",
            "inject_at_hr": 2.0,
            "duration_hrs": 1.0,       # readings 4–5 (truck) -> cold_storage_intervention
            "temp_offset": +4.5,       # 5+4.5=9.5°C, deviation=1.5 -> TEMP_BREACH (not REEFER)
        },
        {
            "type": "DOOR_BREACH",
            "description": "Container lid inadvertently opened at BOM cargo terminal",
            "inject_at_hr": 5.5,
            "duration_hrs": 0.5,       # reading 11 (airport) -> door_breach_agent
            "door_override": True,
        },
        {
            "type": "FLIGHT_CANCEL",
            "description": "BOM->DXB flight cancelled due to sandstorm",
            "inject_at_hr": 6.5,
            "duration_hrs": 1.5,       # readings 13–14 (airport) debounce=2 -> flight_rebooking_agent
            "flight_override": "CANCELLED",
        },
        {
            "type": "SENSOR_SILENCE",
            "description": "IoT tracker battery critically low at DXB hub",
            "inject_at_hr": 12.0,
            "duration_hrs": 0.5,       # reading 24 (airport) -> assume_breach_agent
            "battery_override": 5.0,   # <20% triggers SENSOR_SILENCE
        },
        {
            "type": "REEFER_FAILURE",
            "description": "Refrigeration unit catastrophic failure at DXB hub",
            "inject_at_hr": 13.5,
            "duration_hrs": 1.0,       # readings 27–28 (airport) -> emergency_vehicle_swap
            "temp_offset": +8.5,       # 5+8.5=13.5°C, deviation=5.5 > 3.0 -> REEFER_FAILURE
        },
        {
            "type": "CUSTOMS_HOLD",
            "description": "FDA documentation rejected at JFK — import permit missing",
            "inject_at_hr": 29.0,
            "duration_hrs": 1.5,       # readings 58–61 (airport) debounce=2 -> compliance_escalation_agent
            "customs_override": "HOLD_FDA_DOCS",
        },
        {
            "type": "TRUCK_STALL",
            "description": "Last-mile delivery truck breaks down in Brooklyn",
            "inject_at_hr": 33.0,
            "duration_hrs": 1.5,       # readings 67–68 (truck) debounce=2 -> alternate_carrier_agent
            "speed_override": 0.0,
        },
        {
            "type": "ROAD_ACCIDENT",
            "description": "Rear-end collision on the FDR Drive approach to hospital",
            "inject_at_hr": 34.5,
            "duration_hrs": 0.5,       # reading 69 (truck) -> ai_fallback_agent
            "shock_g_override": 4.5,
            "speed_override": 0.0,
        },
    ],

    # ── Other shipments — single anomaly each ─────────────────────────────
    "SHP-005": [{
        "type": "TRUCK_STALL",
        "description": "Truck breakdown on way to BOM airport",
        "inject_at_hr": 1.5,
        "duration_hrs": 1.0,
        "speed_override": 0.0,
    }],
    "SHP-006": [{
        "type": "TRUCK_STALL",
        "description": "Truck stall on Mumbai-Ahmedabad highway",
        "inject_at_hr": 2.0,
        "duration_hrs": 0.5,
        "speed_override": 0.0,
    }],
    "SHP-007": [{
        "type": "CUSTOMS_HOLD",
        "description": "FDA documentation rejected at JFK",
        "inject_at_hr": 28.0,
        "duration_hrs": 6.0,
        "customs_override": "HOLD_FDA_DOCS",
    }],
    "SHP-008": [{
        "type": "FLIGHT_CANCEL",
        "description": "BOM->DXB flight cancelled due to weather",
        "inject_at_hr": 6.0,
        "duration_hrs": 8.0,
        "flight_override": "CANCELLED",
    }],
    "SHP-009": [{
        "type": "SENSOR_SILENCE",
        "description": "Tracker battery dies mid-flight BOM->DEL",
        "inject_at_hr": 3.0,
        "duration_hrs": 1.0,
        "battery_override": 0.0,
    }],
    "SHP-010": [{
        "type": "DOOR_BREACH",
        "description": "Container opened during DXB layover",
        "inject_at_hr": 11.5,
        "duration_hrs": 0.15,
        "door_override": True,
    }],
}


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def interpolate_coords(start, end, progress):
    return (
        start[0] + (end[0] - start[0]) * progress,
        start[1] + (end[1] - start[1]) * progress,
    )


def get_leg_at_hour(elapsed_hrs, legs):
    """Return the current leg and progress within it for a given route's legs."""
    cumulative = 0.0
    for leg in legs:
        if cumulative + leg["duration_hrs"] > elapsed_hrs:
            progress = (elapsed_hrs - cumulative) / leg["duration_hrs"]
            return leg, max(0.0, min(1.0, progress))
        cumulative += leg["duration_hrs"]
    return legs[-1], 1.0


# ═══════════════════════════════════════════════════════════════════════
# METADATA GENERATION
# ═══════════════════════════════════════════════════════════════════════

def generate_shipment_metadata():
    shipments = []
    base_time = datetime(2026, 4, 5, 8, 0, 0, tzinfo=timezone.utc)

    for i in range(1, 11):
        sid = f"SHP-{i:03d}"
        vaccine = random.choice(VACCINE_PROFILES)
        profile_key = SHIPMENT_ROUTES[sid]
        profile = ROUTE_PROFILES[profile_key]
        hospital = random.choice(profile["hospitals"])

        shipments.append({
            "shipment_id": sid,
            "cargo_type": "pharmaceutical_vaccine",
            "vaccine_type": vaccine["type"],
            "temp_range_min": vaccine["temp_min"],
            "temp_range_max": vaccine["temp_max"],
            "viability_window_hrs": vaccine["viability_hrs"],
            "origin_warehouse": "Mumbai Cold Storage Facility",
            "origin_airport": profile["origin_airport"] or "N/A",
            "transit_hub": profile["transit_hub"] or "N/A",
            "dest_airport": profile["dest_airport"] or "N/A",
            "destination_hospital": hospital,
            "route_profile": profile_key,
            "route_label": profile["label"],
            "total_route_hrs": profile["total_hrs"],
            "carrier": random.choice(CARRIERS),
            "insurance_days": 4,
            "pack_time": (base_time + timedelta(hours=i * 2)).isoformat(),
            "created_at": (base_time + timedelta(hours=i * 2)).isoformat(),
            "status": "in_transit",
        })
    return shipments


# ═══════════════════════════════════════════════════════════════════════
# TELEMETRY GENERATION
# ═══════════════════════════════════════════════════════════════════════

def generate_telemetry(shipments):
    """One reading every 30 min, only for the shipment's actual route duration."""
    all_readings = []
    reading_interval_min = 30

    for shipment in shipments:
        sid = shipment["shipment_id"]
        profile = ROUTE_PROFILES[shipment["route_profile"]]
        legs = profile["legs"]
        total_hrs = profile["total_hrs"]

        pack_time = datetime.fromisoformat(shipment["pack_time"])
        temp_center = (shipment["temp_range_min"] + shipment["temp_range_max"]) / 2
        temp_range  = shipment["temp_range_max"] - shipment["temp_range_min"]

        # Support both single-dict (legacy) and list of anomalies per shipment
        raw = ANOMALY_SCENARIOS.get(sid)
        anomaly_list = raw if isinstance(raw, list) else ([raw] if raw else [])

        total_readings = int((total_hrs * 60) / reading_interval_min) + 1

        for r in range(total_readings):
            elapsed_hrs = (r * reading_interval_min) / 60.0
            ts = pack_time + timedelta(minutes=r * reading_interval_min)
            leg, progress = get_leg_at_hour(elapsed_hrs, legs)
            coords = interpolate_coords(leg["start_coords"], leg["end_coords"], progress)

            # Find the active anomaly window for this reading (first match wins)
            active_anomaly = None
            for anomaly in anomaly_list:
                if anomaly["inject_at_hr"] <= elapsed_hrs < anomaly["inject_at_hr"] + anomaly["duration_hrs"]:
                    active_anomaly = anomaly
                    break

            def _in(atype):
                return active_anomaly is not None and active_anomaly["type"] == atype

            # Temperature
            normal_temp = temp_center + random.uniform(-temp_range * 0.15, temp_range * 0.15)
            if _in("TEMP_BREACH") or _in("REEFER_FAILURE"):
                normal_temp += active_anomaly.get("temp_offset", +5.0)

            # Speed
            if leg["mode"] == "truck":
                speed = random.uniform(40.0, 80.0)
            elif leg["mode"] == "flight":
                speed = random.uniform(800.0, 900.0)
            else:
                speed = 0.0
            if _in("TRUCK_STALL") or _in("ROAD_ACCIDENT"):
                speed = active_anomaly.get("speed_override", 0.0)

            # Shock
            shock = round(random.uniform(0.01, 0.5), 3)
            if _in("ROAD_ACCIDENT"):
                shock = active_anomaly.get("shock_g_override", 4.5)

            # Door
            door_open = False
            if _in("DOOR_BREACH"):
                door_open = active_anomaly.get("door_override", True)

            # Battery
            battery = max(5.0, 100.0 - (elapsed_hrs * 0.8) + random.uniform(-2, 2))
            if _in("SENSOR_SILENCE"):
                battery = active_anomaly.get("battery_override", 0.0)

            # Customs
            customs = "NOT_APPLICABLE"
            if leg["leg"] in ("dest_airport_customs", "origin_airport_wait"):
                customs = "CLEARED"
            if _in("CUSTOMS_HOLD"):
                customs = active_anomaly.get("customs_override", "HOLD_FDA_DOCS")

            # Flight status
            flight_status = "NOT_APPLICABLE"
            if leg["mode"] == "flight":
                flight_status = "IN_AIR"
            elif leg["leg"] in ("origin_airport_wait", "transit_hub_wait"):
                flight_status = "ON_TIME"
            if _in("FLIGHT_CANCEL"):
                flight_status = active_anomaly.get("flight_override", "CANCELLED")

            # Sensor silence: only drop the reading if battery is truly 0
            # (complete signal loss). Low-battery readings are kept so the
            # workflow can detect SENSOR_SILENCE via battery < 20% threshold.
            if _in("SENSOR_SILENCE") and battery <= 0.0:
                continue

            all_readings.append({
                "reading_id":       str(uuid.uuid4())[:8],
                "shipment_id":      sid,
                "timestamp":        ts.isoformat(),
                "elapsed_hrs":      round(elapsed_hrs, 2),
                "leg":              leg["leg"],
                "leg_mode":         leg["mode"],
                "lat":              round(coords[0], 6),
                "lng":              round(coords[1], 6),
                "speed_kmh":        round(speed, 1),
                "temp_c":           round(normal_temp, 2),
                "humidity_pct":     round(random.uniform(35.0, 55.0), 1),
                "shock_g":          shock,
                "door_open":        door_open,
                "battery_pct":      round(battery, 1),
                "flight_status":    flight_status,
                "customs_status":   customs,
                "eta_delta_hrs":    round(random.uniform(-0.5, 0.5), 2),
                "ambient_temp_c":   round(random.uniform(28.0, 38.0), 1),
                "route_alerts":     "NONE",
                "airport_conditions": "NORMAL",
                "carrier_id":       shipment["carrier"],
            })

    return all_readings


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Generating shipment metadata...")
    shipments = generate_shipment_metadata()
    with open(OUTPUT_DIR / "shipments.json", "w") as f:
        json.dump(shipments, f, indent=2)
    print(f"  -> {len(shipments)} shipments written to data/shipments.json")

    print("Generating telemetry readings...")
    telemetry = generate_telemetry(shipments)
    with open(OUTPUT_DIR / "telemetry.json", "w") as f:
        json.dump(telemetry, f, indent=2)
    print(f"  -> {len(telemetry)} readings written to data/telemetry.json")

    print("\nRoute assignments:")
    for sid, profile_key in SHIPMENT_ROUTES.items():
        p = ROUTE_PROFILES[profile_key]
        print(f"  {sid}: {p['label']:35s} ({p['total_hrs']:.1f} hrs)")

    print("\nInjected anomalies:")
    for sid, a in ANOMALY_SCENARIOS.items():
        entries = a if isinstance(a, list) else [a]
        for e in entries:
            print(f"  {sid}: {e['type']:15s} at hour {e['inject_at_hr']:.1f} -- {e['description']}")

    from collections import Counter
    counts = Counter(r["shipment_id"] for r in telemetry)
    print("\nReadings per shipment:")
    for sid in sorted(counts):
        p = ROUTE_PROFILES[SHIPMENT_ROUTES[sid]]
        print(f"  {sid}: {counts[sid]:3d} readings  ({p['label']}, {p['total_hrs']:.1f} hrs)")


if __name__ == "__main__":
    main()
