# 🚛 AI Cargo Monitor — Pharmaceutical Cold-Chain Intelligence

> Real-time multi-agent monitoring for pharmaceutical shipments using LangGraph, Claude AI, and IoT telemetry. Detects anomalies, predicts spoilage risk, and triggers human-approved interventions — all with full GDP/FDA compliance logging.

---

## 🎬 Demo Video

<!-- Add demo video here -->
> **📹 Video coming soon** — Replace this block with your demo video embed.
>
> ```
> [Demo Video Placeholder]
> Record a walkthrough of SHP-003 showing all 8 anomaly types firing in sequence.
> Upload to YouTube/Loom and embed here:
>
> [![Demo Video](https://img.youtube.com/vi/YOUR_VIDEO_ID/0.jpg)](https://www.youtube.com/watch?v=YOUR_VIDEO_ID)
> ```

---

## 🌐 Live Demo (No Setup Required)

Try the interactive demo directly in your browser — no Python, no API key, no installation:

**[▶ Launch Live Demo](https://pranavshashidhara.github.io/AI_cargo_monitoring/)**

> All 10 shipments are available. **Start with SHP-003** to see all 8 anomaly types (Temperature Breach, Door Breach, Flight Cancellation, Sensor Silence, Reefer Failure, Customs Hold, Truck Stall, Road Accident) fire in a single journey.

---

## 🧠 What It Does

This system simulates a real-world pharmaceutical logistics control tower monitoring vaccine shipments from **Mumbai → Dubai → New York**. It uses a **LangGraph multi-agent workflow** to:

| Layer | What happens |
|-------|-------------|
| **Ingest** | IoT telemetry arrives every 30 min (temp, GPS, shock, battery, door sensor) |
| **Detect** | 4 parallel agents scan for anomalies simultaneously |
| **Score** | Risk scoring with FDA/WHO viability window calculation |
| **Orchestrate** | AI decides the right action agent, with debounce + cooldown logic |
| **Act** | Specialist agents handle each failure type with Claude AI reasoning |
| **Approve** | Human operator reviews and approves/rejects every action |
| **Execute** | Reroute, notify stakeholders, reschedule patients, generate insurance docs |
| **Log** | Full GDP/FDA-compliant audit trail |

---

## 🗺️ Route Support

| Route | Distance | Duration | Shipments |
|-------|----------|----------|-----------|
| Mumbai → Dubai → New York | International | 35.5 hrs | SHP-001 to SHP-005, SHP-007, SHP-008, SHP-010 |
| Mumbai → Ahmedabad | Domestic | 5.0 hrs | SHP-006 |
| Mumbai → Delhi | Regional | 6.0 hrs | SHP-009 |

---

## 🔴 Anomaly Types Handled

| Anomaly | Trigger | Action Agent | Map Behaviour |
|---------|---------|-------------|---------------|
| `TEMP_BREACH` (truck) | Temp outside 2–8°C on road | Cold storage divert | Orange line → facility → next checkpoint |
| `TEMP_BREACH` (flight) | Temp breach mid-flight | In-flight alert | ❄️ pin at next landing airport |
| `DOOR_BREACH` | Container opened | Spoilage assessment | Red anomaly pin only |
| `FLIGHT_CANCEL` | Flight status = CANCELLED | Flight rebooking agent | Red anomaly pin only |
| `SENSOR_SILENCE` | Battery < 20% | Assume-breach protocol | Red anomaly pin only |
| `REEFER_FAILURE` | Major temp spike (>3°C over max) | Emergency vehicle swap | Red anomaly pin only |
| `CUSTOMS_HOLD` | HOLD_FDA_DOCS at airport | Compliance escalation + broker | Red anomaly pin only |
| `TRUCK_STALL` | Speed = 0 on truck leg (×2 readings) | Alternate carrier dispatch | Red anomaly pin only |
| `ROAD_ACCIDENT` | Shock > 3g + speed = 0 on truck | AI fallback analysis | Red anomaly pin only |

---

## 🏗️ Architecture

```
IoT Telemetry
     │
     ▼
┌─────────────────────────────────────────────────────┐
│                  LangGraph Workflow                  │
│                                                     │
│  ingest_telemetry                                   │
│       │                                             │
│       ├──► detect_anomaly      ┐                   │
│       ├──► detect_truck_stall  │ parallel           │
│       ├──► monitor_flight      │ fan-out            │
│       └──► monitor_customs     ┘                   │
│                 │                                   │
│           score_risk                                │
│                 │                                   │
│     check_viability_budget                          │
│                 │                                   │
│       check_buffer_warning                          │
│                 │                                   │
│      orchestrate_decision  ◄── debounce + cooldown  │
│             │                                       │
│    ┌────────┴────────┐                              │
│  NOMINAL        ANOMALY                             │
│    │                 │                              │
│ continue     [action agent]  ← Claude AI            │
│ monitoring        │                                 │
│    │         human_approval  ← interrupt()          │
│    │              │                                 │
│    │         execute_reroute                        │
│    │              │                                 │
│    └──────── wait_for_next_reading ◄── loop         │
└─────────────────────────────────────────────────────┘
```

---

## 🚀 Local Setup

### Prerequisites
- Python 3.11+
- An Anthropic API key ([get one here](https://console.anthropic.com))

### 1. Clone & install

```bash
git clone https://github.com/PranavShashidhara/AI_cargo_monitoring.git
cd AI_cargo_monitoring
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

`.env` contents:
```
ANTHROPIC_API_KEY=sk-ant-...

# Optional: Email alerts
EMAIL_FROM=you@gmail.com
EMAIL_PASSWORD=your_app_password
```

### 3. Generate data

```bash
python data/generate_synthetic_data.py
```

### 4. Run the dashboard

```bash
python dashboard.py
```

Open **http://localhost:8050** in your browser.

---

## 📁 Project Structure

```
AI_cargo_monitoring/
├── cargo_monitor_workflow.py   # LangGraph multi-agent workflow (core)
├── dashboard.py                # Python HTTP server + LangGraph integration
├── dashboard.html              # Interactive frontend (Leaflet map, Chart.js)
├── build_static.py             # Builds the GitHub Pages static demo
├── docs/
│   └── index.html              # Static demo (no server required)
├── data/
│   ├── generate_synthetic_data.py  # Synthetic telemetry generator
│   ├── shipments.json          # 10 shipment profiles
│   ├── telemetry.json          # IoT readings for all shipments
│   ├── lookup_tables.py        # Cold storage, carrier, flight lookups
│   └── inventory.json          # Hospital inventory data
├── tests/
│   └── test_agents.py          # 58 unit tests for all agents
├── Plan/
│   └── workflow.html           # Interactive workflow diagram
├── requirements.txt
└── .env.example
```

---

## 🧪 Testing

```bash
python -m pytest tests/test_agents.py -v
```

58 tests covering all agent nodes, anomaly detection, cooldown/debounce logic, and state transitions.

---

## 🗺️ Demo Shipment — SHP-003 (All Failures)

SHP-003 is designed to demonstrate every workflow node in a single journey:

| Hour | Event | Node |
|------|-------|------|
| 2.0h | Temp spike on warehouse truck | `cold_storage_intervention` |
| 5.5h | Container door opened at BOM | `door_breach_agent` |
| 6.5h | BOM→DXB flight cancelled (×2) | `flight_rebooking_agent` |
| 12.0h | IoT battery dies at DXB hub | `assume_breach_agent` |
| 13.5h | Refrigeration unit failure | `emergency_vehicle_swap` |
| 29.0h | FDA docs rejected at JFK (×2) | `compliance_escalation_agent` |
| 33.5h | Truck stalls in Brooklyn (×2) | `alternate_carrier_agent` |
| 34.5h | Road accident on FDR Drive | `ai_fallback_agent` |

---

## 👥 Team

Built at the **UMD AI Agent Hackathon 2026**

---

## 📄 License

MIT
