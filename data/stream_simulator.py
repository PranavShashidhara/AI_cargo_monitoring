"""
Live Stream Simulator
=====================
Replays telemetry data from telemetry.json as a "live stream",
yielding one reading at a time with a configurable delay.

Usage in your LangGraph workflow:
    from data.stream_simulator import TelemetryStream

    stream = TelemetryStream(shipment_id="SHP-003", speed=10.0)
    for reading in stream:
        result = graph.invoke(reading)

Usage standalone (demo):
    python data/stream_simulator.py --shipment SHP-003 --speed 50
"""

import json
import time
import argparse
from pathlib import Path
from typing import Generator


DATA_DIR = Path(__file__).parent


class TelemetryStream:
    """Simulates a live telemetry stream by replaying stored readings.

    Args:
        shipment_id: Filter to a single shipment (or None for all).
        speed: Playback speed multiplier (1.0 = real-time 30-min intervals,
               10.0 = 10x faster, 0 = no delay / instant replay).
        data_path: Path to telemetry.json.
    """

    def __init__(
        self,
        shipment_id: str | None = None,
        speed: float = 10.0,
        data_path: Path | None = None,
    ):
        self.shipment_id = shipment_id
        self.speed = speed
        self.data_path = data_path or DATA_DIR / "telemetry.json"
        self._readings = self._load()

    def _load(self) -> list[dict]:
        with open(self.data_path) as f:
            data = json.load(f)
        if self.shipment_id:
            data = [r for r in data if r["shipment_id"] == self.shipment_id]
        data.sort(key=lambda r: r["timestamp"])
        return data

    def __iter__(self) -> Generator[dict, None, None]:
        """Yield readings one at a time, sleeping between them."""
        real_interval_sec = 30 * 60  # 30 minutes between readings
        delay = real_interval_sec / self.speed if self.speed > 0 else 0

        for i, reading in enumerate(self._readings):
            yield reading
            if delay > 0 and i < len(self._readings) - 1:
                time.sleep(delay)

    def __len__(self) -> int:
        return len(self._readings)

    def get_shipment_metadata(self) -> dict | None:
        """Load the matching shipment record from shipments.json."""
        meta_path = DATA_DIR / "shipments.json"
        if not meta_path.exists():
            return None
        with open(meta_path) as f:
            shipments = json.load(f)
        for s in shipments:
            if s["shipment_id"] == self.shipment_id:
                return s
        return None


def main():
    parser = argparse.ArgumentParser(description="Replay telemetry as live stream")
    parser.add_argument("--shipment", default="SHP-003", help="Shipment ID to stream")
    parser.add_argument("--speed", type=float, default=50.0, help="Playback speed multiplier")
    args = parser.parse_args()

    stream = TelemetryStream(shipment_id=args.shipment, speed=args.speed)
    meta = stream.get_shipment_metadata()

    print(f"Streaming {len(stream)} readings for {args.shipment}")
    if meta:
        print(f"  Vaccine: {meta['vaccine_type']}")
        print(f"  Range:   {meta['temp_range_min']}C to {meta['temp_range_max']}C")
        print(f"  Route:   {meta['origin_airport']} -> {meta['transit_hub']} -> {meta['dest_airport']}")
    print(f"  Speed:   {args.speed}x\n")

    for reading in stream:
        status = ""
        if reading["temp_c"] > 8.0 and meta and meta["temp_range_max"] <= 8.0:
            status = " ** TEMP BREACH **"
        elif reading["speed_kmh"] == 0 and reading["leg_mode"] == "truck":
            status = " ** STALL **"
        elif reading["door_open"]:
            status = " ** DOOR BREACH **"
        elif reading["customs_status"].startswith("HOLD"):
            status = " ** CUSTOMS HOLD **"
        elif reading["flight_status"] == "CANCELLED":
            status = " ** FLIGHT CANCELLED **"

        print(
            f"  [{reading['timestamp']}] "
            f"leg={reading['leg']:25s} "
            f"temp={reading['temp_c']:7.2f}C "
            f"speed={reading['speed_kmh']:6.1f} "
            f"bat={reading['battery_pct']:5.1f}% "
            f"door={'OPEN' if reading['door_open'] else 'shut':4s}"
            f"{status}"
        )


if __name__ == "__main__":
    main()
