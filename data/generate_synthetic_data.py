"""
Synthetic Telemetry Generator
=============================
Generates realistic pharma cold-chain shipment data: India -> USA
10 shipments, ~200 readings each (every 30 min over 4 days).
Anomalies are injected at specific points for demo scenarios.

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
# ROUTE DEFINITION: India -> DXB hub -> JFK -> Hospital
# ═══════════════════════════════════════════════════════════════════════

ROUTE_LEGS = [
    {
        "leg": "warehouse_to_airport",
        "description": "Truck: Mumbai warehouse -> BOM airport",
        "start_coords": (19.0760, 72.8777),   # Mumbai
        "end_coords": (19.0896, 72.8656),      # BOM airport
        "duration_hrs": 3.0,
        "mode": "truck",
    },
    {
        "leg": "origin_airport_wait",
        "description": "BOM airport: customs + loading",
        "start_coords": (19.0896, 72.8656),
        "end_coords": (19.0896, 72.8656),
        "duration_hrs": 4.0,
        "mode": "airport",
    },
    {
        "leg": "flight_to_hub",
        "description": "Flight: BOM -> DXB",
        "start_coords": (19.0896, 72.8656),
        "end_coords": (25.2532, 55.3657),      # DXB
        "duration_hrs": 3.5,
        "mode": "flight",
    },
    {
        "leg": "transit_hub_wait",
        "description": "DXB hub: layover + cold storage",
        "start_coords": (25.2532, 55.3657),
        "end_coords": (25.2532, 55.3657),
        "duration_hrs": 4.0,
        "mode": "airport",
    },
    {
        "leg": "flight_to_dest",
        "description": "Flight: DXB -> JFK",
        "start_coords": (25.2532, 55.3657),
        "end_coords": (40.6413, -73.7781),     # JFK
        "duration_hrs": 14.0,
        "mode": "flight",
    },
    {
        "leg": "dest_airport_customs",
        "description": "JFK: US customs + FDA inspection",
        "start_coords": (40.6413, -73.7781),
        "end_coords": (40.6413, -73.7781),
        "duration_hrs": 5.0,
        "mode": "airport",
    },
    {
        "leg": "last_mile_delivery",
        "description": "Truck: JFK -> NYC General Hospital",
        "start_coords": (40.6413, -73.7781),
        "end_coords": (40.7128, -74.0060),     # Manhattan
        "duration_hrs": 2.0,
        "mode": "truck",
    },
]

TOTAL_ROUTE_HRS = sum(l["duration_hrs"] for l in ROUTE_LEGS)  # ~35.5 hrs

VACCINE_PROFILES = [
    {"type": "standard_flu", "temp_min": 2.0, "temp_max": 8.0, "viability_hrs": 96},
    {"type": "mRNA_pfizer", "temp_min": -80.0, "temp_max": -60.0, "viability_hrs": 72},
    {"type": "standard_mmr", "temp_min": 2.0, "temp_max": 8.0, "viability_hrs": 96},
]

CARRIERS = ["DHL_COLD", "FEDEX_PHARMA", "UPS_TEMP", "KUEHNE_NAGEL", "CEVA_LOGISTICS"]

HOSPITALS = [
    "NYC General Hospital", "Mount Sinai", "NYU Langone",
    "Columbia Presbyterian", "Bellevue Hospital",
]

# ═══════════════════════════════════════════════════════════════════════
# ANOMALY INJECTION DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

ANOMALY_SCENARIOS = {
    "SHP-003": {
        "type": "TEMP_BREACH",
        "description": "Temperature spike at hour 14 during DXB->JFK flight",
        "inject_at_hr": 14.0,
        "duration_hrs": 2.0,
        "temp_offset": +6.0,
    },
    "SHP-005": {
        "type": "TRUCK_STALL",
        "description": "Truck breakdown on way to BOM airport",
        "inject_at_hr": 1.5,
        "duration_hrs": 1.0,
        "speed_override": 0.0,
    },
    "SHP-007": {
        "type": "CUSTOMS_HOLD",
        "description": "FDA documentation rejected at JFK",
        "inject_at_hr": 28.0,
        "duration_hrs": 6.0,
        "customs_override": "HOLD_FDA_DOCS",
    },
    "SHP-008": {
        "type": "FLIGHT_CANCEL",
        "description": "BOM->DXB flight cancelled due to weather",
        "inject_at_hr": 6.0,
        "duration_hrs": 8.0,
        "flight_override": "CANCELLED",
    },
    "SHP-009": {
        "type": "SENSOR_SILENCE",
        "description": "Tracker battery dies mid-flight DXB->JFK",
        "inject_at_hr": 18.0,
        "duration_hrs": 3.0,
        "battery_override": 0.0,
    },
    "SHP-010": {
        "type": "DOOR_BREACH",
        "description": "Container opened during DXB layover",
        "inject_at_hr": 11.5,
        "duration_hrs": 0.15,
        "door_override": True,
    },
}


def interpolate_coords(start, end, progress):
    """Linear interpolation between two (lat, lng) points."""
    return (
        start[0] + (end[0] - start[0]) * progress,
        start[1] + (end[1] - start[1]) * progress,
    )


def get_leg_at_hour(elapsed_hrs):
    """Return the current route leg and progress within it."""
    cumulative = 0.0
    for leg in ROUTE_LEGS:
        if cumulative + leg["duration_hrs"] > elapsed_hrs:
            progress = (elapsed_hrs - cumulative) / leg["duration_hrs"]
            return leg, max(0.0, min(1.0, progress))
        cumulative += leg["duration_hrs"]
    return ROUTE_LEGS[-1], 1.0


def generate_shipment_metadata():
    """Create 10 shipment records."""
    shipments = []
    base_time = datetime(2026, 4, 5, 8, 0, 0, tzinfo=timezone.utc)

    for i in range(1, 11):
        vaccine = random.choice(VACCINE_PROFILES)
        shipments.append({
            "shipment_id": f"SHP-{i:03d}",
            "cargo_type": "pharmaceutical_vaccine",
            "vaccine_type": vaccine["type"],
            "temp_range_min": vaccine["temp_min"],
            "temp_range_max": vaccine["temp_max"],
            "viability_window_hrs": vaccine["viability_hrs"],
            "origin_warehouse": "Mumbai Cold Storage Facility",
            "origin_airport": "BOM",
            "transit_hub": "DXB",
            "dest_airport": "JFK",
            "destination_hospital": random.choice(HOSPITALS),
            "carrier": random.choice(CARRIERS),
            "insurance_days": 4,
            "pack_time": (base_time + timedelta(hours=i * 2)).isoformat(),
            "created_at": (base_time + timedelta(hours=i * 2)).isoformat(),
            "status": "in_transit",
        })
    return shipments


def generate_telemetry(shipments):
    """Generate ~200 telemetry readings per shipment (every 30 min, 4 days)."""
    all_readings = []
    reading_interval_min = 30
    total_readings = int((4 * 24 * 60) / reading_interval_min)  # 192 readings

    for shipment in shipments:
        sid = shipment["shipment_id"]
        pack_time = datetime.fromisoformat(shipment["pack_time"])
        temp_center = (shipment["temp_range_min"] + shipment["temp_range_max"]) / 2
        temp_range = shipment["temp_range_max"] - shipment["temp_range_min"]
        anomaly = ANOMALY_SCENARIOS.get(sid)

        for r in range(total_readings):
            elapsed_hrs = (r * reading_interval_min) / 60.0
            ts = pack_time + timedelta(minutes=r * reading_interval_min)
            leg, progress = get_leg_at_hour(elapsed_hrs % TOTAL_ROUTE_HRS)
            coords = interpolate_coords(
                leg["start_coords"], leg["end_coords"], progress
            )

            in_anomaly_window = (
                anomaly
                and anomaly["inject_at_hr"]
                <= elapsed_hrs
                < anomaly["inject_at_hr"] + anomaly["duration_hrs"]
            )

            # --- Temperature ---
            normal_temp = temp_center + random.uniform(
                -temp_range * 0.15, temp_range * 0.15
            )
            if in_anomaly_window and anomaly["type"] == "TEMP_BREACH":
                normal_temp += anomaly["temp_offset"]

            # --- Speed ---
            if leg["mode"] == "truck":
                speed = random.uniform(40.0, 80.0)
            elif leg["mode"] == "flight":
                speed = random.uniform(800.0, 900.0)
            else:
                speed = 0.0

            if in_anomaly_window and anomaly["type"] == "TRUCK_STALL":
                speed = anomaly["speed_override"]

            # --- Door ---
            door_open = False
            if in_anomaly_window and anomaly["type"] == "DOOR_BREACH":
                door_open = anomaly["door_override"]

            # --- Battery ---
            battery = max(5.0, 100.0 - (elapsed_hrs * 0.8) + random.uniform(-2, 2))
            if in_anomaly_window and anomaly["type"] == "SENSOR_SILENCE":
                battery = anomaly["battery_override"]

            # --- Customs ---
            customs = "NOT_APPLICABLE"
            if leg["leg"] in ("dest_airport_customs", "origin_airport_wait"):
                customs = "CLEARED"
            if in_anomaly_window and anomaly["type"] == "CUSTOMS_HOLD":
                customs = anomaly["customs_override"]

            # --- Flight status ---
            flight_status = "NOT_APPLICABLE"
            if leg["mode"] == "flight":
                flight_status = "IN_AIR"
            elif leg["leg"] in ("origin_airport_wait", "transit_hub_wait"):
                flight_status = "ON_TIME"
            if in_anomaly_window and anomaly["type"] == "FLIGHT_CANCEL":
                flight_status = anomaly["flight_override"]

            # --- Sensor silence: skip writing reading entirely ---
            if in_anomaly_window and anomaly["type"] == "SENSOR_SILENCE":
                continue

            reading = {
                "reading_id": str(uuid.uuid4())[:8],
                "shipment_id": sid,
                "timestamp": ts.isoformat(),
                "elapsed_hrs": round(elapsed_hrs, 2),
                "leg": leg["leg"],
                "leg_mode": leg["mode"],
                "lat": round(coords[0], 6),
                "lng": round(coords[1], 6),
                "speed_kmh": round(speed, 1),
                "temp_c": round(normal_temp, 2),
                "humidity_pct": round(random.uniform(35.0, 55.0), 1),
                "shock_g": round(random.uniform(0.01, 0.5), 3),
                "door_open": door_open,
                "battery_pct": round(battery, 1),
                "flight_status": flight_status,
                "customs_status": customs,
                "eta_delta_hrs": round(random.uniform(-0.5, 0.5), 2),
                "ambient_temp_c": round(random.uniform(28.0, 38.0), 1),
                "route_alerts": "NONE",
                "airport_conditions": "NORMAL",
                "carrier_id": shipment["carrier"],
            }
            all_readings.append(reading)

    return all_readings


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

    # Print anomaly summary
    print("\nInjected anomalies:")
    for sid, a in ANOMALY_SCENARIOS.items():
        print(f"  {sid}: {a['type']} at hour {a['inject_at_hr']} - {a['description']}")

    # Print per-shipment stats
    from collections import Counter
    counts = Counter(r["shipment_id"] for r in telemetry)
    print(f"\nReadings per shipment:")
    for sid in sorted(counts):
        print(f"  {sid}: {counts[sid]} readings")


if __name__ == "__main__":
    main()
