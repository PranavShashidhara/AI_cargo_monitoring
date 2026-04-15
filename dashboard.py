"""
AI Cargo Monitor — Live Dashboard (LangGraph stream mode)
==========================================================
Run:   python dashboard.py
Opens: http://localhost:8050

The graph LOOPS through the entire shipment journey:
  ingest → detect → score → orchestrate → action/continue
  → wait_for_next_reading (interrupt) → feed next reading → loop
Multiple anomalies, cold-storage diversions, and reroutes are handled
in a single persistent graph execution per shipment.
"""

import http.server
import json
import sys
import webbrowser
import threading
import traceback as tb
from urllib.parse import urlparse
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from cargo_monitor_workflow import build_cargo_monitor_graph, notify_stakeholders

# ── Load data ──
with open(ROOT / "data" / "shipments.json") as f:
    SHIPMENTS = json.load(f)
with open(ROOT / "data" / "telemetry.json") as f:
    TELEMETRY = json.load(f)

# TOTAL_ROUTE_HRS is now read per-shipment from shipments.json (total_route_hrs field)
# This dict is populated lazily when each shipment is first started
_SHIPMENT_ROUTE_HRS: dict = {}

# ── Compile the LangGraph in stream mode ──
checkpointer = InMemorySaver()
builder = build_cargo_monitor_graph(mode="stream")
graph = builder.compile(checkpointer=checkpointer)

print("  LangGraph compiled (mode=stream, interrupt-based loop)")

thread_counter = 0
graph_lock = threading.Lock()


def _enrich(state: dict) -> dict:
    """Add ETA / buffer / delay fields to the graph output.

    When a next_reading interrupt is present, its accumulated delay
    is more current than the invoke result (which snapshots pre-return).
    """
    intr = state.get("_interrupt")
    if isinstance(intr, dict) and "total_accumulated_delay_hrs" in intr:
        state["total_accumulated_delay_hrs"] = intr["total_accumulated_delay_hrs"]

    elapsed = state.get("elapsed_hrs", 0.0)
    delays = state.get("total_accumulated_delay_hrs", 0.0)
    viability = state.get("viability_window_hrs", 96.0)

    # Look up per-shipment route hours (dynamic — not hardcoded)
    sid = state.get("shipment_id", "")
    total_route_hrs = _SHIPMENT_ROUTE_HRS.get(sid, 35.5)

    # ETA = time remaining on normal route + any accumulated delay from interventions
    remaining = max(0.0, total_route_hrs - elapsed)
    state["eta_destination_hrs"] = round(remaining + delays, 1)

    # Buffer = how much MORE delay we can absorb before viability expires.
    # Formula: viability_window - normal_route_time - accumulated_intervention_delays
    # This only decreases when an intervention adds delay — NOT as time passes normally.
    state["buffer_hrs"] = round(max(0.0, viability - total_route_hrs - delays), 1)

    state["delay_added_hrs"] = state.get(
        "time_impact_hrs", state.get("delay_hrs", 0.0)
    )
    state["routed_to"] = state.get("anomaly_type", "NOMINAL")
    return state


def _get_interrupt_from_result(result: dict) -> dict | None:
    """Extract interrupt payload from the __interrupt__ channel in invoke result."""
    dunder = result.get("__interrupt__")
    if dunder and isinstance(dunder, (list, tuple)) and len(dunder) > 0:
        item = dunder[0]
        return item.value if hasattr(item, "value") else item
    return None


def start_shipment(body: dict) -> dict:
    """Start processing a shipment — graph runs the first reading.

    The graph processes the first reading through the full pipeline.
    If NOMINAL: pauses at wait_for_next_reading interrupt.
    If anomaly: pauses at human_approval_gateway interrupt.
    """
    global thread_counter
    sid = body["shipment_id"]
    shipment = next(s for s in SHIPMENTS if s["shipment_id"] == sid)
    readings = [r for r in TELEMETRY if r["shipment_id"] == sid]

    with graph_lock:
        thread_counter += 1
        tid = f"ship-{sid}-{thread_counter}"

    # Cache this shipment's actual route duration for ETA/buffer calculations
    _SHIPMENT_ROUTE_HRS[sid] = float(shipment.get("total_route_hrs", 35.5))

    first = readings[0]
    initial_state = {
        **first,
        "vaccine_type": shipment.get("vaccine_type", "standard_flu"),
        "viability_window_hrs": shipment.get("viability_window_hrs", 96),
        "destination_hospital": shipment.get("destination_hospital", ""),
        "origin_airport": shipment.get("origin_airport", "BOM"),
        "dest_airport": shipment.get("dest_airport", "JFK"),
        "transit_hub": shipment.get("transit_hub", "DXB"),
        "temp_range_min": shipment.get("temp_range_min", 2),
        "temp_range_max": shipment.get("temp_range_max", 8),
        "insurance_days": shipment.get("insurance_days", 4),
        "pack_time": shipment.get("pack_time", first.get("timestamp", "")),
        "reading_index": 0,
        "total_readings": len(readings),
    }

    config = {"configurable": {"thread_id": tid}}
    result = graph.invoke(initial_state, config)

    state = dict(result)
    state.pop("__interrupt__", None)
    state["_thread_id"] = tid
    state["_interrupt"] = _get_interrupt_from_result(result)
    return _enrich(state)


def next_reading(body: dict) -> dict:
    """Feed the next telemetry reading to the looping graph.

    Resumes from the wait_for_next_reading interrupt. The graph then
    loops back to ingest_telemetry with the new reading data.
    """
    tid = body["_thread_id"]
    reading = body["reading"]
    config = {"configurable": {"thread_id": tid}}

    result = graph.invoke(Command(resume=reading), config)

    state = dict(result)
    state.pop("__interrupt__", None)
    state["_thread_id"] = tid
    state["_interrupt"] = _get_interrupt_from_result(result)
    return _enrich(state)


def execute_decision(body: dict) -> dict:
    """Resume after human approval of an anomaly action.

    After execution agents run, the graph continues to
    wait_for_next_reading, pausing for the next reading.
    """
    tid = body["_thread_id"]
    config = {"configurable": {"thread_id": tid}}

    human_response = {
        "decision": body.get("decision", "REJECT"),
        "operator_id": body.get("approved_by", "dashboard_operator"),
        "timestamp": body.get("approved_at", ""),
    }

    result = graph.invoke(Command(resume=human_response), config)

    intr_val = _get_interrupt_from_result(result)

    state = dict(result)
    state.pop("__interrupt__", None)
    state["_thread_id"] = tid
    state["_interrupt"] = intr_val
    return _enrich(state)


def run_notify(state: dict) -> dict:
    """Explicit notification trigger (separate from graph flow)."""
    state.update(notify_stakeholders(state))
    return state


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/dashboard.html"):
            self._serve_file(ROOT / "dashboard.html", "text/html")
        elif path == "/api/shipments":
            self._json(SHIPMENTS)
        elif path.startswith("/api/telemetry/"):
            sid = path.rsplit("/", 1)[-1]
            self._json([r for r in TELEMETRY if r["shipment_id"] == sid])
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        path = urlparse(self.path).path
        try:
            if path == "/api/start":
                self._json(start_shipment(body))
            elif path == "/api/next":
                self._json(next_reading(body))
            elif path == "/api/execute":
                self._json(execute_decision(body))
            elif path == "/api/notify":
                self._json(run_notify(body))
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"error": str(e), "trace": tb.format_exc()})

    def _json(self, data):
        payload = json.dumps(data, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_file(self, filepath, ctype):
        with open(filepath, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt, *args):
        pass


def main():
    port = 8050
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"\n  AI Cargo Monitor Dashboard (LangGraph stream mode)")
    print(f"  Running at {url}")
    print(f"  Press Ctrl+C to stop\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
