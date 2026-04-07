"""Generate realistic flights, patients, and inventory data files."""
import json, random
from datetime import datetime, timezone, timedelta

random.seed(42)

HOSPITALS = [
    "Mount Sinai",
    "NYC General Hospital",
    "NYU Langone",
    "Bellevue Hospital",
    "Columbia Presbyterian",
]

VACCINE_MAP = {
    "Mount Sinai":           "standard_flu",
    "NYC General Hospital":  "standard_mmr",
    "NYU Langone":           "standard_mmr",
    "Bellevue Hospital":     "standard_mmr",
    "Columbia Presbyterian": "standard_mmr",
}

PHYSICIANS = {
    "Mount Sinai":           ["Dr. Sarah Chen", "Dr. Marcus Webb", "Dr. Priya Nair"],
    "NYC General Hospital":  ["Dr. James Okafor", "Dr. Linda Park", "Dr. Tom Russo"],
    "NYU Langone":           ["Dr. Aisha Patel", "Dr. David Kim", "Dr. Rachel Gomez"],
    "Bellevue Hospital":     ["Dr. Carlos Vega", "Dr. Fiona Marsh", "Dr. Henry Liu"],
    "Columbia Presbyterian": ["Dr. Nina Brown", "Dr. Omar Hassan", "Dr. Elena Vasquez"],
}

FIRST = ["Emma","Liam","Sophia","Noah","Olivia","Mason","Ava","Ethan",
         "Isabella","Logan","Mia","Lucas","Charlotte","James","Amelia",
         "Benjamin","Harper","Oliver","Evelyn","Elijah","Abigail","Sebastian"]
LAST  = ["Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
         "Martinez","Wilson","Anderson","Taylor","Thomas","Hernandez","Moore",
         "Martin","Jackson","Thompson","White","Lopez","Lee","Harris"]

DEPT = ["Immunization Clinic", "Pediatric Vaccines", "Internal Medicine", "Preventive Care"]

BASE = datetime(2026, 4, 7, 8, 0, tzinfo=timezone.utc)
APPT_OFFSETS = [
    0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5,
    8, 8.5, 9, 9.5, 10, 10.5, 11, 11.5,
    24, 24.5, 25, 25.5, 26, 27, 28, 28.5, 29, 30,
    48, 48.5, 49, 49.5, 50, 51, 52, 52.5, 53, 54,
    72, 72.5, 73, 73.5, 74, 75,
]
APPT_TIMES = [BASE + timedelta(hours=h) for h in APPT_OFFSETS]


# ── PATIENTS ──────────────────────────────────────────────────────────
patients = []
pid = 1
for hospital in HOSPITALS:
    vtype = VACCINE_MAP[hospital]
    docs  = PHYSICIANS[hospital]
    times = sorted(random.sample(APPT_TIMES, 16))
    for t in times:
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"
        patients.append({
            "patient_id":       f"PT-{pid:03d}",
            "hospital":         hospital,
            "vaccine_type":     vtype,
            "appointment_utc":  t.isoformat().replace("+00:00", "Z"),
            "patient_name":     name,
            "physician":        random.choice(docs),
            "department":       random.choice(DEPT),
            "appointment_type": "Scheduled Vaccination",
            "contact_email":    name.lower().replace(" ", ".") + "@example-patient.com",
            "dose_number":      random.choice([1, 1, 1, 2]),
        })
        pid += 1

with open("data/patients.json", "w") as f:
    json.dump(patients, f, indent=2)
print(f"patients.json  -> {len(patients)} appointments")


# ── FLIGHTS ────────────────────────────────────────────────────────────
DAILY_FLIGHTS = [
    {"flight_id": "EK-508", "airline": "Emirates SkyCargo",     "origin": "BOM", "destination": "DXB", "dep_h":  0.5, "dur_h":  2.5, "cap": 5000, "temp_range": "2-8C / -80C"},
    {"flight_id": "EK-510", "airline": "Emirates SkyCargo",     "origin": "BOM", "destination": "DXB", "dep_h":  8.0, "dur_h":  2.5, "cap": 4800, "temp_range": "2-8C / -80C"},
    {"flight_id": "AI-983", "airline": "Air India Cargo",       "origin": "BOM", "destination": "DXB", "dep_h": 16.25,"dur_h":  2.5, "cap": 3200, "temp_range": "2-8C"},
    {"flight_id": "IX-190", "airline": "Air Arabia Cargo",      "origin": "BOM", "destination": "DXB", "dep_h": 20.0, "dur_h":  2.75,"cap": 2000, "temp_range": "2-8C"},
    {"flight_id": "EK-201", "airline": "Emirates SkyCargo",     "origin": "DXB", "destination": "JFK", "dep_h":  2.0, "dur_h": 14.0, "cap": 8000, "temp_range": "2-8C / -80C"},
    {"flight_id": "EK-203", "airline": "Emirates SkyCargo",     "origin": "DXB", "destination": "JFK", "dep_h": 14.5, "dur_h": 14.0, "cap": 7500, "temp_range": "2-8C / -80C"},
    {"flight_id": "QR-701", "airline": "Qatar Airways Cargo",   "origin": "DXB", "destination": "JFK", "dep_h":  6.0, "dur_h": 14.5, "cap": 6000, "temp_range": "2-8C"},
    {"flight_id": "TK-009", "airline": "Turkish Cargo",         "origin": "DXB", "destination": "JFK", "dep_h": 22.0, "dur_h": 13.5, "cap": 5500, "temp_range": "2-8C"},
]

flights = []
start_day = datetime(2026, 4, 5, tzinfo=timezone.utc)
for day_offset in range(8):
    day = start_day + timedelta(days=day_offset)
    for tmpl in DAILY_FLIGHTS:
        dep = day + timedelta(hours=tmpl["dep_h"])
        arr = dep + timedelta(hours=tmpl["dur_h"])
        used = random.randint(1500, tmpl["cap"] - 200)
        avail = tmpl["cap"] - used
        status = random.choices(
            ["SCHEDULED", "SCHEDULED", "SCHEDULED", "DELAYED", "CANCELLED"],
            weights=[70, 70, 70, 15, 5]
        )[0]
        flights.append({
            "flight_id":             f"{tmpl['flight_id']}/{day.strftime('%d%b').upper()}",
            "base_flight":           tmpl["flight_id"],
            "airline":               tmpl["airline"],
            "origin":                tmpl["origin"],
            "destination":           tmpl["destination"],
            "departure_utc":         dep.isoformat().replace("+00:00", "Z"),
            "arrival_utc":           arr.isoformat().replace("+00:00", "Z"),
            "duration_hrs":          tmpl["dur_h"],
            "cold_chain_certified":  True,
            "cargo_capacity_kg":     tmpl["cap"],
            "available_capacity_kg": avail,
            "temp_range":            tmpl["temp_range"],
            "status":                status,
        })

with open("data/flights.json", "w") as f:
    json.dump(flights, f, indent=2)
print(f"flights.json   -> {len(flights)} flights over 8 days")


# ── INVENTORY ──────────────────────────────────────────────────────────
SHIPMENT_MAP = {
    ("NYC General Hospital",  "standard_mmr"): ("SHP-001", "2026-04-07T01:00:00Z"),
    ("NYU Langone",           "standard_mmr"): ("SHP-002", "2026-04-07T03:00:00Z"),
    ("Mount Sinai",           "standard_flu"): ("SHP-003", "2026-04-07T05:00:00Z"),
    ("Bellevue Hospital",     "standard_mmr"): ("SHP-004", "2026-04-07T07:00:00Z"),
    ("Columbia Presbyterian", "standard_mmr"): ("SHP-005", "2026-04-07T09:00:00Z"),
    ("NYC General Hospital",  "standard_flu"): ("SHP-006", "2026-04-07T11:00:00Z"),
    ("Bellevue Hospital",     "standard_flu"): ("SHP-007", "2026-04-07T13:00:00Z"),
}

inventory = []
for hospital in HOSPITALS:
    for vtype in ["standard_flu", "standard_mmr"]:
        daily = random.randint(8, 20)
        current = random.randint(10, 60)
        minimum = random.randint(15, 25)
        maximum = random.randint(200, 400)
        days_left = round(current / daily, 1)
        shp_info = SHIPMENT_MAP.get((hospital, vtype))
        inventory.append({
            "hospital":                   hospital,
            "vaccine_type":               vtype,
            "current_units":              current,
            "minimum_threshold":          minimum,
            "maximum_capacity":           maximum,
            "daily_usage_rate":           daily,
            "days_of_supply":             days_left,
            "units_on_order":             150,
            "expected_delivery_shipment": shp_info[0] if shp_info else None,
            "expected_delivery_utc":      shp_info[1] if shp_info else None,
            "last_updated_utc":           "2026-04-07T04:00:00Z",
            "critical_low":               days_left < 2.0,
        })

with open("data/inventory.json", "w") as f:
    json.dump(inventory, f, indent=2)
print(f"inventory.json -> {len(inventory)} records")
print("\nDone.")
