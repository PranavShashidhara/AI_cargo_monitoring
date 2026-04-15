"""
Lookup Tables — Reference Data for All Agents
==============================================
Hardcoded facility data, carrier lists, flight schedules, vaccine thresholds,
and WHO emergency facilities used by action/execution agents.

In production, these would come from a database or external API.
"""

import math

# ═══════════════════════════════════════════════════════════════════════
# VACCINE THRESHOLDS (FDA / WHO guidelines)
# ═══════════════════════════════════════════════════════════════════════

VACCINE_THRESHOLDS = {
    "standard_flu": {
        "temp_min": 2.0,
        "temp_max": 8.0,
        "critical_above": 8.0,
        "critical_below": 0.0,
        "max_excursion_hrs": 1.0,
        "spoilage_rate_per_hr": 0.25,
        "regulation": "FDA 21 CFR 211.142 — Storage at 2-8C",
    },
    "mRNA_pfizer": {
        "temp_min": -80.0,
        "temp_max": -60.0,
        "critical_above": -60.0,
        "critical_below": -90.0,
        "max_excursion_hrs": 0.25,
        "spoilage_rate_per_hr": 0.80,
        "regulation": "FDA EUA — Pfizer-BioNTech storage at -80C to -60C",
    },
    "standard_mmr": {
        "temp_min": 2.0,
        "temp_max": 8.0,
        "critical_above": 8.0,
        "critical_below": 0.0,
        "max_excursion_hrs": 1.0,
        "spoilage_rate_per_hr": 0.25,
        "regulation": "FDA 21 CFR 211.142 — Storage at 2-8C, freeze-sensitive",
    },
}

DEFAULT_VACCINE_THRESHOLD = VACCINE_THRESHOLDS["standard_flu"]

# ═══════════════════════════════════════════════════════════════════════
# COLD STORAGE FACILITIES (along India → USA route)
# ═══════════════════════════════════════════════════════════════════════

COLD_STORAGE_FACILITIES = [
    {"id": "CS-MUM-01", "name": "Mumbai PharmaStore", "lat": 19.10, "lng": 72.90,
     "type": "warehouse", "capacity_available": True, "temp_range": "2-8C"},
    {"id": "CS-BOM-01", "name": "BOM Airport Cold Room A", "lat": 19.089, "lng": 72.866,
     "type": "airport", "capacity_available": True, "temp_range": "2-8C / -80C"},
    {"id": "CS-DXB-01", "name": "Dubai DXB Pharma Vault", "lat": 25.253, "lng": 55.366,
     "type": "hub", "capacity_available": True, "temp_range": "2-8C / -80C"},
    {"id": "CS-DXB-02", "name": "Dubai South Cold Chain", "lat": 24.896, "lng": 55.161,
     "type": "warehouse", "capacity_available": True, "temp_range": "2-8C"},
    {"id": "CS-JFK-01", "name": "JFK Pharma Cold Vault", "lat": 40.641, "lng": -73.778,
     "type": "airport", "capacity_available": True, "temp_range": "2-8C / -80C"},
    {"id": "CS-EWR-01", "name": "Newark Cold Logistics", "lat": 40.693, "lng": -74.174,
     "type": "warehouse", "capacity_available": True, "temp_range": "2-8C"},
    {"id": "CS-NYC-01", "name": "Manhattan Pharma Depot", "lat": 40.750, "lng": -73.990,
     "type": "warehouse", "capacity_available": True, "temp_range": "2-8C"},
]

# ═══════════════════════════════════════════════════════════════════════
# CARRIER FLEET (available for vehicle swap / alternate carrier)
# ═══════════════════════════════════════════════════════════════════════

AVAILABLE_CARRIERS = [
    {"carrier_id": "DHL_COLD_02", "name": "DHL Cold Chain Express",
     "contact": "+1-800-225-5345", "response_mins": 45,
     "coverage": ["Mumbai", "Dubai", "New York"], "vehicle_type": "reefer_truck"},
    {"carrier_id": "FEDEX_PHARMA_03", "name": "FedEx Healthcare Priority",
     "contact": "+1-800-463-3339", "response_mins": 30,
     "coverage": ["Mumbai", "Dubai", "New York"], "vehicle_type": "reefer_truck"},
    {"carrier_id": "UPS_TEMP_01", "name": "UPS Temperature True",
     "contact": "+1-800-742-5877", "response_mins": 60,
     "coverage": ["Dubai", "New York"], "vehicle_type": "reefer_truck"},
    {"carrier_id": "CEVA_COLD_04", "name": "CEVA Cold Chain",
     "contact": "+91-22-6191-0000", "response_mins": 40,
     "coverage": ["Mumbai"], "vehicle_type": "reefer_truck"},
    {"carrier_id": "KN_PHARMA_05", "name": "Kuehne+Nagel PharmaChain",
     "contact": "+971-4-299-6000", "response_mins": 35,
     "coverage": ["Dubai", "New York"], "vehicle_type": "reefer_truck"},
]

# ═══════════════════════════════════════════════════════════════════════
# FLIGHT SCHEDULE (available cargo flights on the route)
# ═══════════════════════════════════════════════════════════════════════

FLIGHT_SCHEDULE = [
    {"flight_id": "EK-508", "route": "BOM-DXB", "departure": "06:00",
     "arrival": "08:30", "carrier": "Emirates SkyCargo", "cargo_cold": True},
    {"flight_id": "EK-510", "route": "BOM-DXB", "departure": "14:00",
     "arrival": "16:30", "carrier": "Emirates SkyCargo", "cargo_cold": True},
    {"flight_id": "AI-983", "route": "BOM-DXB", "departure": "22:15",
     "arrival": "00:45+1", "carrier": "Air India Cargo", "cargo_cold": True},
    {"flight_id": "EK-201", "route": "DXB-JFK", "departure": "10:00",
     "arrival": "16:00", "carrier": "Emirates SkyCargo", "cargo_cold": True},
    {"flight_id": "EK-203", "route": "DXB-JFK", "departure": "22:30",
     "arrival": "04:30+1", "carrier": "Emirates SkyCargo", "cargo_cold": True},
    {"flight_id": "QR-701", "route": "DXB-JFK", "departure": "08:00",
     "arrival": "14:00", "carrier": "Qatar Cargo (via DOH)", "cargo_cold": True},
]

# ═══════════════════════════════════════════════════════════════════════
# CUSTOMS BROKERS (for escalation)
# ═══════════════════════════════════════════════════════════════════════

CUSTOMS_BROKERS = [
    {"name": "Pharma Customs Solutions Inc.",
     "contact": "+1-212-555-0134", "email": "urgent@pharmacustoms.com",
     "specialization": "FDA pharmaceutical imports", "response_hrs": 1},
    {"name": "Global Trade Compliance LLC",
     "contact": "+1-212-555-0198", "email": "pharma@gtcompliance.com",
     "specialization": "cold chain documentation", "response_hrs": 2},
]

REQUIRED_FDA_DOCS = [
    "FDA Form 2877 (Entry/Delivery Notice)",
    "Commercial Invoice with NDC codes",
    "Packing List with cold chain certification",
    "Certificate of Analysis (CoA)",
    "Drug Listing (FDA Form 2657)",
    "Importer of Record documentation",
    "Temperature logger data export (PDF)",
]

# ═══════════════════════════════════════════════════════════════════════
# WHO EMERGENCY FACILITIES (for resupply)
# ═══════════════════════════════════════════════════════════════════════

WHO_EMERGENCY_FACILITIES = [
    {"name": "WHO NYC Emergency Vaccine Reserve",
     "location": "New York, NY", "lat": 40.750, "lng": -73.970,
     "eta_hrs": 2.0, "vaccines_available": ["standard_flu", "standard_mmr"]},
    {"name": "CDC Strategic National Stockpile - NE",
     "location": "Newark, NJ", "lat": 40.735, "lng": -74.172,
     "eta_hrs": 3.0, "vaccines_available": ["standard_flu", "mRNA_pfizer", "standard_mmr"]},
    {"name": "NYC DOHMH Vaccine Depot",
     "location": "Long Island City, NY", "lat": 40.742, "lng": -73.923,
     "eta_hrs": 1.5, "vaccines_available": ["standard_flu", "standard_mmr"]},
]

# ═══════════════════════════════════════════════════════════════════════
# BACKUP HOSPITALS (when primary cannot receive)
# ═══════════════════════════════════════════════════════════════════════

BACKUP_HOSPITALS = {
    "NYC General Hospital":     {"backup": "Mount Sinai Cold Storage",      "lat": 40.790, "lng": -73.952},
    "Mount Sinai":              {"backup": "NYU Langone Pharmacy",          "lat": 40.742, "lng": -73.974},
    "NYU Langone":              {"backup": "Columbia Presbyterian",         "lat": 40.841, "lng": -73.942},
    "Columbia Presbyterian":    {"backup": "Bellevue Hospital Pharmacy",    "lat": 40.739, "lng": -73.975},
    "Bellevue Hospital":        {"backup": "NYC General Hospital",          "lat": 40.713, "lng": -74.002},
}

# ═══════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearest_cold_storage(lat: float, lng: float) -> dict:
    """Return the nearest cold-storage facility to the given coordinates."""
    best = None
    best_dist = float("inf")
    for facility in COLD_STORAGE_FACILITIES:
        if not facility["capacity_available"]:
            continue
        d = haversine_km(lat, lng, facility["lat"], facility["lng"])
        if d < best_dist:
            best_dist = d
            best = {**facility, "distance_km": round(d, 1)}
    return best


def find_available_carrier(current_carrier_id: str, region: str = "") -> dict | None:
    """Find an alternate carrier, excluding the current one."""
    for c in AVAILABLE_CARRIERS:
        if c["carrier_id"] == current_carrier_id:
            continue
        if region and region not in " ".join(c["coverage"]).lower():
            continue
        return c
    return AVAILABLE_CARRIERS[0]


def find_next_flight(route_prefix: str, current_flight_id: str = "") -> dict | None:
    """Find the next available cargo flight on a route segment."""
    for f in FLIGHT_SCHEDULE:
        if route_prefix in f["route"] and f["flight_id"] != current_flight_id:
            return f
    return None


def find_nearest_who_facility(vaccine_type: str) -> dict | None:
    """Find the nearest WHO emergency facility that stocks this vaccine."""
    for f in WHO_EMERGENCY_FACILITIES:
        if vaccine_type in f["vaccines_available"]:
            return f
    return WHO_EMERGENCY_FACILITIES[0] if WHO_EMERGENCY_FACILITIES else None


def get_vaccine_threshold(vaccine_type: str) -> dict:
    """Get FDA/WHO temperature thresholds for a vaccine type."""
    return VACCINE_THRESHOLDS.get(vaccine_type, DEFAULT_VACCINE_THRESHOLD)
