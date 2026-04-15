"""
build_static.py
---------------
Generates docs/index.html — a fully self-contained static demo of the
AI Cargo Monitor that runs entirely in the browser (no Python backend needed).

Run:  python build_static.py
Output: docs/index.html  (ready for GitHub Pages)
"""
import json, re
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
DOCS.mkdir(exist_ok=True)

# ── Load data ──────────────────────────────────────────────────────────────
shipments = json.load(open(ROOT / "data" / "shipments.json"))
telemetry  = json.load(open(ROOT / "data" / "telemetry.json"))
dashboard  = open(ROOT / "dashboard.html", encoding="utf-8").read()

ships_js = json.dumps(shipments, separators=(',', ':'))
telem_js = json.dumps(telemetry,  separators=(',', ':'))

# ── Mock backend engine ────────────────────────────────────────────────────
MOCK_ENGINE = r"""
/* ═══════════════════════════════════════════════════════════════
   STATIC DEMO — Mock Backend Engine
   Replaces the Python/LangGraph server so the demo runs fully
   in the browser with no server required.
   ═══════════════════════════════════════════════════════════════ */

const MOCK_SHIPMENTS = """ + ships_js + """;
const MOCK_TELEMETRY = """ + telem_js + """;

/* ── cold storage lookup (mirrors Python lookup_tables.py) ── */
const COLD_STORAGE_FACILITIES = [
  {id:'BCS-001',name:'Mumbai Bio Cold',lat:19.082,lng:72.871,distance_km:3.2},
  {id:'DXB-CS1',name:'Dubai Pharma Cold Hub',lat:25.261,lng:55.372,distance_km:2.1},
  {id:'JFK-CS1',name:'JFK Cold Chain Depot',lat:40.648,lng:-73.790,distance_km:1.8},
  {id:'DEL-CS1',name:'Delhi Pharma Storage',lat:28.562,lng:77.108,distance_km:1.5},
  {id:'AMD-CS1',name:'Ahmedabad Bio Storage',lat:23.030,lng:72.565,distance_km:0.9},
];
function findNearestCS(lat,lng){
  return COLD_STORAGE_FACILITIES.slice().sort((a,b)=>{
    const d=(f)=>Math.hypot(f.lat-lat,f.lng-lng);
    return d(a)-d(b);
  })[0];
}

const AIRPORT_COORDS = {
  BOM:{lat:19.090,lng:72.866,name:'BOM Airport'},
  DXB:{lat:25.253,lng:55.366,name:'DXB Hub'},
  JFK:{lat:40.641,lng:-73.778,name:'JFK Airport'},
  DEL:{lat:28.556,lng:77.100,name:'DEL Airport'},
};

const LEG_NEXT_CHECKPOINT = {
  warehouse_to_airport: 'origin_airport',
  origin_airport_wait:  'origin_airport',
  transit_hub_wait:     'transit_hub',
  dest_airport_customs: 'dest_airport',
  last_mile_delivery:   'destination',
  road_transit:         'destination',
};

function nextCheckpoint(leg, shipment){
  const hint = LEG_NEXT_CHECKPOINT[leg] || 'destination';
  if(hint==='origin_airport'){
    const ap=AIRPORT_COORDS[shipment.origin_airport]||AIRPORT_COORDS.BOM;
    return {name:ap.name,lat:ap.lat,lng:ap.lng};
  }
  if(hint==='transit_hub'){
    const ap=AIRPORT_COORDS[shipment.transit_hub]||AIRPORT_COORDS.DXB;
    return {name:ap.name,lat:ap.lat,lng:ap.lng};
  }
  if(hint==='dest_airport'){
    const ap=AIRPORT_COORDS[shipment.dest_airport]||AIRPORT_COORDS.JFK;
    return {name:ap.name,lat:ap.lat,lng:ap.lng};
  }
  return {name:'Destination Hospital',lat:0,lng:0};
}

/* ── Anomaly detection (JS mirror of Python detect_anomaly) ── */
function detectAnomaly(r, shipment){
  const tMax=shipment.temp_range_max, tMin=shipment.temp_range_min;
  if(r.door_open) return 'DOOR_BREACH';
  if(r.battery_pct>0 && r.battery_pct<20) return 'SENSOR_SILENCE';
  if(r.shock_g>3.0 && r.speed_kmh===0 && r.leg_mode==='truck') return 'ROAD_ACCIDENT';
  if(r.temp_c>tMax){
    return (r.temp_c-tMax)>(tMax-tMin)*0.5 ? 'REEFER_FAILURE' : 'TEMP_BREACH';
  }
  if(r.temp_c<tMin) return 'TEMP_BREACH';
  if(r.flight_status==='CANCELLED') return 'FLIGHT_CANCEL';
  if(r.customs_status && r.customs_status.startsWith('HOLD')) return 'CUSTOMS_HOLD';
  if(r.speed_kmh===0 && r.leg_mode==='truck') return '_STALL';
  return 'NOMINAL';
}

const IMMEDIATE = new Set(['DOOR_BREACH','REEFER_FAILURE','TEMP_BREACH','SENSOR_SILENCE','ROAD_ACCIDENT','NOT_VIABLE']);
const DEBOUNCE_THRESHOLD = 2;

function orchestrate(raw, ts){
  let eff = (raw==='_STALL') ? 'TRUCK_STALL' : raw;
  // cooldown suppression
  if(ts.anomaly_handled && eff===ts.active_anomaly && ts.anomaly_cooldown_readings>0){
    ts.anomaly_cooldown_readings--;
    return 'NOMINAL';
  }
  ts.anomaly_cooldown_readings = Math.max(0, (ts.anomaly_cooldown_readings||0)-1);
  if(eff==='NOMINAL'){ts.consecutive_anomaly_count=0; return 'NOMINAL';}
  // debounce for logistics anomalies
  if(!IMMEDIATE.has(eff)){
    if(ts.active_anomaly===eff){
      ts.consecutive_anomaly_count=(ts.consecutive_anomaly_count||0)+1;
      if(ts.consecutive_anomaly_count<DEBOUNCE_THRESHOLD) return 'NOMINAL';
    } else {
      ts.active_anomaly=eff; ts.consecutive_anomaly_count=1; return 'NOMINAL';
    }
  }
  return eff;
}

/* ── Pre-written AI responses per anomaly type ── */
function anomalyPreset(anomaly, r, shipment){
  const tMax=shipment.temp_range_max, tMin=shipment.temp_range_min;
  const cs = findNearestCS(r.lat||0,r.lng||0);
  const cp = nextCheckpoint(r.leg||'',shipment);
  const presets = {
    TEMP_BREACH:{
      severity:'HIGH', risk_score:75, spoilage_prob:0.12,
      recommended_action:`Divert to ${cs.name} — temperature ${r.temp_c.toFixed(1)}°C exceeds ${tMax}°C`,
      risk_narrative:`Temperature excursion detected at ${r.temp_c.toFixed(1)}°C. Safe range is ${tMin}–${tMax}°C. Immediate cold storage intervention required to prevent vaccine spoilage. Diverting to nearest GDP-compliant facility.`,
      cold_storage_location:`${cs.name} (${cs.id}) - ${cs.distance_km}km away`,
      cold_storage_lat:cs.lat, cold_storage_lng:cs.lng,
      next_checkpoint_name:cp.name, next_checkpoint_lat:cp.lat, next_checkpoint_lng:cp.lng,
      detour_time_hrs:1.0, time_impact_hrs:1.0, in_flight_temp_alert:false,
    },
    DOOR_BREACH:{
      severity:'CRITICAL', risk_score:88, spoilage_prob:0.35,
      recommended_action:'Seal container immediately. Inspect vaccine integrity. Document for GDP compliance.',
      risk_narrative:`Container door opened at ${r.leg?.replace(/_/g,' ')}. Cold chain integrity compromised. Ambient temperature exposure risk. Sealing container and initiating spoilage assessment. All affected doses must be quarantined pending thermal excursion analysis.`,
      time_impact_hrs:0.5,
    },
    FLIGHT_CANCEL:{
      severity:'HIGH', risk_score:62, spoilage_prob:0.05,
      recommended_action:'Rebook on next available cold-chain certified cargo flight. Arrange hub cold storage.',
      risk_narrative:`Flight cancelled. Next available cold-chain certified cargo flight identified. Shipment will be held at ${shipment.transit_hub||'hub'} cold storage facility during the delay. Viability window has sufficient buffer to absorb the delay.`,
      new_flight_id:'EK-510', new_departure_time:'08:00 UTC', hub_storage_needed:true,
      time_impact_hrs:8.0,
    },
    SENSOR_SILENCE:{
      severity:'HIGH', risk_score:70, spoilage_prob:0.18,
      recommended_action:'Assume worst-case temperature breach. Initiate emergency contact with carrier for manual inspection.',
      risk_narrative:`IoT sensor battery critically low (${r.battery_pct?.toFixed(0)}%). Last known temperature was within range but sensor gap creates regulatory exposure. Assuming breach protocol per SOP-COLD-007. Manual inspection requested from carrier.`,
      time_impact_hrs:1.0,
    },
    REEFER_FAILURE:{
      severity:'CRITICAL', risk_score:95, spoilage_prob:0.55,
      recommended_action:'Emergency vehicle swap required. Temperature excursion critical — refrigeration unit failure confirmed.',
      risk_narrative:`Refrigeration unit failure detected. Temperature ${r.temp_c.toFixed(1)}°C far exceeds safe range. Catastrophic cold chain failure. Immediate vehicle swap with a pre-cooled replacement required. Estimated transfer time: 45 minutes.`,
      new_vehicle_id:'VEH-ALT-8F3A', transfer_eta_mins:45,
      time_impact_hrs:1.5,
    },
    CUSTOMS_HOLD:{
      severity:'HIGH', risk_score:55, spoilage_prob:0.04,
      recommended_action:'Engage licensed customs broker. Submit missing FDA import permit and temperature certificates.',
      risk_narrative:`Customs hold at ${r.leg?.replace(/_/g,' ')||'destination airport'}. Missing FDA import permit and cold-chain temperature deviation certificate. Broker Pharmserv International selected based on prior FDA clearance record. Documents being re-submitted. Estimated clearance: 4–6 hours.`,
      hold_reason:'FDA_DOCS', hold_duration_est:5.0,
      time_impact_hrs:5.0,
    },
    TRUCK_STALL:{
      severity:'HIGH', risk_score:60, spoilage_prob:0.06,
      recommended_action:'Dispatch alternate refrigerated carrier. ETA for pickup: 35 minutes.',
      risk_narrative:`Delivery truck stalled at ${r.leg?.replace(/_/g,' ')}. Mechanical failure confirmed. Alternate refrigerated carrier identified with available capacity. Pickup estimated in 35 minutes. Cold chain maintained via onboard battery backup.`,
      alternate_carrier:'FrostLine Logistics (ALT-002)', pickup_eta_mins:35,
      time_impact_hrs:1.2,
    },
    ROAD_ACCIDENT:{
      severity:'CRITICAL', risk_score:85, spoilage_prob:0.20,
      recommended_action:'Emergency services dispatched. Assess cargo integrity. Arrange alternate carrier and cold storage.',
      risk_narrative:`High-impact collision detected (shock: ${r.shock_g?.toFixed(1)}g). Vehicle immobilised. Emergency services contacted. Physical inspection of vaccine containers required for breakage and temperature integrity. Alternate carrier being dispatched to incident location.`,
      ai_diagnosis:'Rear-impact collision based on shock telemetry pattern. Vehicle immobilised.',
      ai_reasoning:'Shock signature (>4g) combined with immediate speed-zero consistent with rear-end or side-impact event. Recommend emergency protocol activation.',
      time_impact_hrs:2.5,
    },
  };
  return presets[anomaly] || {severity:'MEDIUM',risk_score:50,recommended_action:'Monitor situation',time_impact_hrs:0};
}

/* ── Thread state machine ── */
const mockThreads = {};
let mockTc = 0;

function mockBuildState(shipment, r, idx, prev){
  const elapsed = r.elapsed_hrs||0;
  const totalHrs = shipment.total_route_hrs||35.5;
  const delays = prev.total_accumulated_delay_hrs||0;
  const remaining = Math.max(0, totalHrs - elapsed);
  return {
    ...prev,
    // reading fields
    shipment_id: r.shipment_id,
    reading_id: r.reading_id,
    timestamp: r.timestamp,
    elapsed_hrs: elapsed,
    leg: r.leg,
    leg_mode: r.leg_mode,
    lat: r.lat, lng: r.lng,
    speed_kmh: r.speed_kmh,
    temp_c: r.temp_c,
    normalized_temp_c: r.temp_c,
    humidity_pct: r.humidity_pct,
    shock_g: r.shock_g,
    door_open: r.door_open,
    battery_pct: r.battery_pct,
    flight_status: r.flight_status,
    customs_status: r.customs_status,
    carrier_id: r.carrier_id,
    // computed
    reading_index: idx,
    total_readings: prev.total_readings,
    eta_destination_hrs: Math.round((remaining+delays)*10)/10,
    buffer_hrs: Math.round(Math.max(0, (shipment.viability_window_hrs||96) - totalHrs - delays)*10)/10,
    total_accumulated_delay_hrs: delays,
    shipment_viable: true,
    alert_class: (r.temp_c>shipment.temp_range_max||r.temp_c<shipment.temp_range_min)?'CRITICAL':'NOMINAL',
    anomaly_type:'NOMINAL',severity:'LOW',risk_score:10,
    in_flight_temp_alert:false, resuming_from_cold_storage:false,
    cascade_triggered:false,
    spoilage_prob:0,
  };
}

function mockProcessReading(tid, r, idx){
  const th = mockThreads[tid];
  const s = th.shipment;
  const base = mockBuildState(s, r, idx, th.state);

  // If at cold storage, override GPS to facility and check recovery
  if(th.state.at_cold_storage && th.state.cold_storage_lat){
    base.lat = th.state.cold_storage_lat;
    base.lng = th.state.cold_storage_lng;
    base.cold_storage_lat = th.state.cold_storage_lat;
    base.cold_storage_lng = th.state.cold_storage_lng;
    base.cold_storage_location = th.state.cold_storage_location;
    base.next_checkpoint_name = th.state.next_checkpoint_name;
    base.next_checkpoint_lat  = th.state.next_checkpoint_lat;
    base.next_checkpoint_lng  = th.state.next_checkpoint_lng;
    // check if temp is back to normal
    if(r.temp_c >= s.temp_range_min && r.temp_c <= s.temp_range_max){
      base.at_cold_storage = false;
      base.resuming_from_cold_storage = true;
      th.state.at_cold_storage = false;
    } else {
      base.at_cold_storage = true;
      base.resuming_from_cold_storage = false;
    }
  } else {
    base.at_cold_storage = false;
  }

  const rawAnomaly = detectAnomaly(r, s);
  const effAnomaly = orchestrate(rawAnomaly, th.state);

  if(effAnomaly === 'NOMINAL' || base.at_cold_storage){
    th.state = {...base, ...th.state,
      at_cold_storage: base.at_cold_storage,
      resuming_from_cold_storage: base.resuming_from_cold_storage,
      cold_storage_lat: th.state.at_cold_storage ? th.state.cold_storage_lat : 0,
      cold_storage_lng: th.state.at_cold_storage ? th.state.cold_storage_lng : 0,
      cold_storage_location: th.state.at_cold_storage ? th.state.cold_storage_location : '',
      next_checkpoint_name: th.state.at_cold_storage ? th.state.next_checkpoint_name : '',
      next_checkpoint_lat: th.state.at_cold_storage ? th.state.next_checkpoint_lat : 0,
      next_checkpoint_lng: th.state.at_cold_storage ? th.state.next_checkpoint_lng : 0,
      lat: base.lat, lng: base.lng,
      elapsed_hrs: base.elapsed_hrs, leg: base.leg, leg_mode: base.leg_mode,
      normalized_temp_c: base.normalized_temp_c, temp_c: base.temp_c,
      reading_index: idx, alert_class: base.alert_class,
      eta_destination_hrs: base.eta_destination_hrs, buffer_hrs: base.buffer_hrs,
      anomaly_type:'NOMINAL', severity:'LOW', risk_score:10, spoilage_prob:0,
    };
    const nextIdx = idx+1;
    const delivered = nextIdx >= th.state.total_readings || r.leg==='dest_delivery';
    if(delivered){
      th.state.shipment_delivered = true;
      return {...th.state, shipment_delivered:true, _thread_id:tid};
    }
    return {
      ...th.state,
      _thread_id: tid,
      _interrupt:{type:'next_reading',reading_index:nextIdx,
        total_readings:th.state.total_readings,
        total_accumulated_delay_hrs:th.state.total_accumulated_delay_hrs},
    };
  }

  // Anomaly path
  const preset = anomalyPreset(effAnomaly, r, s);
  const isFlightBreachInFlight = effAnomaly==='TEMP_BREACH' && r.leg_mode==='flight';

  th.state = {
    ...base,
    ...th.state,
    lat: base.lat, lng: base.lng,
    elapsed_hrs: base.elapsed_hrs, leg: base.leg, leg_mode: base.leg_mode,
    normalized_temp_c: base.normalized_temp_c, temp_c: base.temp_c,
    reading_index: idx, alert_class:'CRITICAL',
    anomaly_type: effAnomaly,
    active_anomaly: effAnomaly,
    anomaly_handled: false,
    eta_destination_hrs: base.eta_destination_hrs, buffer_hrs: base.buffer_hrs,
    ...preset,
    in_flight_temp_alert: isFlightBreachInFlight,
  };

  return {...th.state, _thread_id:tid};
}

function mockStart(body){
  const sid = body.shipment_id;
  const shipment = MOCK_SHIPMENTS.find(s=>s.shipment_id===sid);
  const readings  = MOCK_TELEMETRY.filter(r=>r.shipment_id===sid);
  const tid = `ship-${sid}-${++mockTc}`;
  mockThreads[tid] = {
    shipment, readings,
    state:{
      shipment_id:sid,
      vaccine_type:shipment.vaccine_type,
      temp_range_min:shipment.temp_range_min,
      temp_range_max:shipment.temp_range_max,
      viability_window_hrs:shipment.viability_window_hrs,
      destination_hospital:shipment.destination_hospital,
      origin_airport:shipment.origin_airport,
      dest_airport:shipment.dest_airport,
      transit_hub:shipment.transit_hub,
      total_route_hrs:shipment.total_route_hrs,
      reading_index:0, total_readings:readings.length,
      anomaly_cooldown_readings:0, consecutive_anomaly_count:0,
      active_anomaly:'NOMINAL', anomaly_handled:false,
      at_cold_storage:false, resuming_from_cold_storage:false,
      in_flight_temp_alert:false,
      total_accumulated_delay_hrs:0, buffer_warning_issued:false,
      cold_storage_lat:0, cold_storage_lng:0, cold_storage_location:'',
      next_checkpoint_name:'', next_checkpoint_lat:0, next_checkpoint_lng:0,
      shipment_viable:true, shipment_delivered:false,
    }
  };
  threadId = tid;
  return mockProcessReading(tid, readings[0], 0);
}

function mockNext(body){
  const tid = body._thread_id;
  const th  = mockThreads[tid];
  const r   = body.reading;
  const idx = (th.state.reading_index||0)+1;
  return mockProcessReading(tid, r, idx);
}

function mockExecute(body){
  const tid = body._thread_id;
  const th  = mockThreads[tid];
  const anomaly = th.state.anomaly_type;
  const r = th.readings[th.state.reading_index] || {};

  // Mark handled + cooldown
  th.state.anomaly_handled = true;
  th.state.anomaly_cooldown_readings = 4;
  th.state.consecutive_anomaly_count = 0;

  const delay = th.state.time_impact_hrs||0;
  th.state.total_accumulated_delay_hrs = (th.state.total_accumulated_delay_hrs||0) + delay;

  // Cold storage tracking for truck TEMP_BREACH
  if(anomaly==='TEMP_BREACH' && th.state.leg_mode!=='flight'){
    th.state.at_cold_storage = true;
  }

  const nextIdx = th.state.reading_index + 1;
  return {
    ...th.state,
    reroute_confirmed: true,
    decision: 'APPROVED',
    _thread_id: tid,
    _interrupt:{
      type:'next_reading',
      reading_index: nextIdx,
      total_readings: th.state.total_readings,
      total_accumulated_delay_hrs: th.state.total_accumulated_delay_hrs,
    },
  };
}

/* ── Fetch intercept ── */
const _realFetch = window.fetch.bind(window);
window.fetch = async function(url, opts){
  const path = (typeof url==='string'?url:url.url).split('?')[0];
  if(path==='/api/shipments'){
    return new Response(JSON.stringify(MOCK_SHIPMENTS),{status:200,headers:{'Content-Type':'application/json'}});
  }
  if(path.startsWith('/api/telemetry/')){
    const sid = path.split('/').pop();
    const data = MOCK_TELEMETRY.filter(r=>r.shipment_id===sid);
    return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
  }
  if(path==='/api/start'){
    const b=JSON.parse(opts.body);
    return new Response(JSON.stringify(mockStart(b)),{status:200,headers:{'Content-Type':'application/json'}});
  }
  if(path==='/api/next'){
    const b=JSON.parse(opts.body);
    return new Response(JSON.stringify(mockNext(b)),{status:200,headers:{'Content-Type':'application/json'}});
  }
  if(path==='/api/execute'){
    const b=JSON.parse(opts.body);
    return new Response(JSON.stringify(mockExecute(b)),{status:200,headers:{'Content-Type':'application/json'}});
  }
  if(path==='/api/notify') return new Response('{}',{status:200});
  return _realFetch(url, opts);
};
"""

# ── Inject mock engine into dashboard.html ─────────────────────────────────
# Replace <script> opening with <script> + mock engine
# The mock engine must come BEFORE the dashboard JS so fetch is overridden
# before init() is called.
INJECT_MARKER = "<script>\n/* ═══════ STATE ═══════ */"
INJECT_WITH   = "<script>\n" + MOCK_ENGINE + "\n/* ═══════ STATE ═══════ */"

if INJECT_MARKER not in dashboard:
    raise RuntimeError("Could not find injection point in dashboard.html")

static_html = dashboard.replace(INJECT_MARKER, INJECT_WITH, 1)

# Update title to indicate demo mode
static_html = static_html.replace(
    "<title>AI Cargo Monitor</title>",
    "<title>AI Cargo Monitor — Live Demo</title>"
)

# Update subtitle
static_html = static_html.replace(
    "Cold-Chain &bull; India &rarr; USA",
    "Live Demo &bull; No server required"
)

# Write output
out = DOCS / "index.html"
out.write_text(static_html, encoding="utf-8")
print(f"Built: {out}  ({out.stat().st_size//1024} KB)")
print("Push docs/ to GitHub and enable Pages on the main branch.")
