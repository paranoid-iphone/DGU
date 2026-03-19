"""
PowerWizard Web Dashboard — HTTP server (stdlib only, Python 3.7+)
Reads PW_MODE env var: "demo" or "real"

Real mode architecture:
  PollManager  → EndpointWorker (one per gateway) → sequential Modbus reads
  EndpointWorker → SnapshotStore (partial updates after each block)
  HTTP handler → SnapshotStore.get_snapshot() — always instant, no I/O
"""
from __future__ import annotations

import json
import math
import os
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler

# ─── Mode ─────────────────────────────────────────────────────────────────────

PW_MODE = os.environ.get("PW_MODE", "demo")
PW_PORT = int(os.environ.get("PW_PORT", "8000"))

# ─── Real mode init ───────────────────────────────────────────────────────────

_store = None
_poll_manager = None

if PW_MODE == "real":
    try:
        from pw_backend import DEVICES
        from snapshot_store import SnapshotStore
        from poll_manager import PollManager

        _store = SnapshotStore(DEVICES, mode="real")
        _poll_manager = PollManager(DEVICES, _store)
        print("[INFO] Real mode: {} device(s) configured.".format(len(DEVICES)))
    except ImportError as e:
        print("[WARN] Real mode init failed: {}".format(e))

# ─── Demo snapshot ────────────────────────────────────────────────────────────

_DEMO_T0 = time.time()


def _demo_scenario_index():
    return int((time.time() - _DEMO_T0) / 30) % 6


def _elapsed_h():
    return (time.time() - _DEMO_T0) / 3600.0


def _make_demo_device(name, slave_id, scenario):
    t = time.time()
    wave  = math.sin(t * 0.3  + slave_id)
    wave2 = math.sin(t * 0.17 + slave_id * 2)

    rpm_base  = 1500.0
    freq_base = 50.0
    load_base = 45.0 + wave * 15.0
    fuel      = max(5.0, min(100.0, 62.0 - slave_id * 8 + wave2 * 5))
    coolant   = 78.0 + wave * 8.0
    battery   = 13.2 + wave2 * 0.4
    oil_p     = 280.0 + wave * 20.0
    kwh       = 3200.0 + slave_id * 900 + _elapsed_h() * 15
    hours     = 1800.0 + slave_id * 450 + _elapsed_h()

    if scenario == 1 and slave_id == 1:
        freq_base = 47.2
    elif scenario == 2 and slave_id == 2:
        freq_base = 0.0
    elif scenario == 3 and slave_id == 1:
        fuel = 22.0
    elif scenario == 4 and slave_id == 2:
        fuel = 9.0
    elif scenario == 5:
        rpm_base = freq_base = load_base = 0.0

    running = rpm_base > 200

    fuel_st = ("critical" if fuel <= 15.0 else "warning" if fuel <= 30.0 else "normal")
    freq_st = ("critical" if running and freq_base == 0.0
               else "warning" if running and (freq_base < 49.0 or freq_base > 51.0)
               else "ok")
    coolant_st = ("critical" if coolant > 95.0 else "warning" if coolant > 85.0 else "ok")
    load_st    = ("critical" if load_base > 85.0 else "warning" if load_base > 70.0 else "ok")
    batt_st    = ("critical" if battery < 11.5 else "warning" if battery < 12.0 else "ok")

    parameters = {
        "battery_voltage":    {"title": "Напряжение АКБ",       "value": round(battery, 2), "unit": "V",   "status": batt_st},
        "gen_avg_frequency":  {"title": "Частота генератора",   "value": round(freq_base + wave2 * 0.05 if freq_base > 0 else 0.0, 3), "unit": "Hz",  "status": freq_st},
        "gen_voltage":        {"title": "Напряжение генератора","value": round(400.0 + wave * 5 if running else 0.0, 1), "unit": "V",   "status": "ok"},
        "coolant_temp":       {"title": "Температура охл.жид.", "value": round(coolant, 2), "unit": "°C",  "status": coolant_st},
        "engine_oil_pressure":{"title": "Давление масла",       "value": round(oil_p if running else 0.0, 1), "unit": "kPa", "status": "ok"},
        "engine_rpm":         {"title": "ЧВД",                  "value": round(rpm_base + wave * 10 if running else 0.0, 1), "unit": "rpm", "status": "ok"},
        "total_percent_kw":   {"title": "Нагрузка %",           "value": round(max(0.0, load_base) if running else 0.0, 1), "unit": "%",   "status": load_st if running else "ok"},
        "fuel_level":         {"title": "Уровень топлива",      "value": round(fuel, 1), "unit": "%", "status": fuel_st if fuel_st != "normal" else "ok", "fuel_status": fuel_st},
        "energy_kwh":         {"title": "Электроэнергия",       "value": round(kwh, 1),  "unit": "kWh",   "status": "ok"},
        "energy_kvarh":       {"title": "Реактивная энергия",   "value": round(kwh * 0.02, 1), "unit": "kVArh", "status": "ok"},
        "engine_hours":       {"title": "Моточасы двигателя",   "value": round(hours, 2),"unit": "h",    "status": "ok"},
    }

    statuses  = [p.get("status", "ok") for p in parameters.values()]
    agg       = ("critical" if "critical" in statuses else "warning" if "warning" in statuses else "ok")
    ok_count  = sum(1 for s in statuses if s == "ok")
    err_count = sum(1 for s in statuses if s in ("critical", "warning", "error"))

    return {
        "name": name, "slave_id": slave_id,
        "connection_status": "ok", "aggregate_status": agg,
        "parameters": parameters,
        "summary": {"ok": ok_count, "fid": 0, "errors": err_count},
    }


def build_demo_snapshot():
    scenario = _demo_scenario_index()
    devices  = {
        d["name"]: _make_demo_device(d["name"], d["slave_id"], scenario)
        for d in [
            {"name": "DG-1", "slave_id": 1},
            {"name": "DG-2", "slave_id": 2},
            {"name": "DG-3", "slave_id": 3},
            {"name": "DG-4", "slave_id": 4},
            {"name": "DG-5", "slave_id": 5},
        ]
    }
    successful = sum(1 for d in devices.values() if d["connection_status"] == "ok")
    return {
        "timestamp": int(time.time() * 1000),
        "backend_status": "ok",
        "devices": devices,
        "summary": {"successful_devices": successful, "total_devices": len(devices)},
        "mode": "demo",
        "demo_scenario": scenario,
    }


# ─── Snapshot dispatcher ──────────────────────────────────────────────────────

def get_snapshot():
    if PW_MODE == "real":
        if _store is None:
            return {
                "timestamp": int(time.time() * 1000),
                "backend_status": "error",
                "error": "Backend not initialized",
                "devices": {}, "summary": {"successful_devices": 0, "total_devices": 0},
                "mode": "real",
            }
        return _store.get_snapshot()
    return build_demo_snapshot()


# ─── HTTP handler ─────────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ico":  "image/x-icon",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        if args and len(args) >= 2 and str(args[1]).startswith(("4", "5")):
            print("[HTTP] {} {} {}".format(self.address_string(), self.requestline, args[1]))

    def safe_write(self, data):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return
        self.safe_write(body)

    def serve_static(self, path):
        import posixpath
        path = posixpath.normpath(path.split("?")[0])
        if path in ("/", ""):
            path = "/index.html"
        file_path = os.path.join(STATIC_DIR, path.lstrip("/"))
        if not os.path.isfile(file_path):
            self.send_error(404, "Not Found")
            return
        ext = os.path.splitext(file_path)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        with open(file_path, "rb") as f:
            body = f.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return
        self.safe_write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/snapshot":
            try:
                self.send_json(get_snapshot())
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
            return

        if path == "/api/health":
            self.send_json({
                "status": "ok" if (_store is not None or PW_MODE == "demo") else "error",
                "mode": PW_MODE,
                "backend_ready": _store is not None or PW_MODE == "demo",
            })
            return

        self.serve_static(path if path != "/" else "/index.html")

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run():
    if PW_MODE == "real" and _poll_manager is not None:
        _poll_manager.start()

    server = ThreadingServer(("0.0.0.0", PW_PORT), Handler)

    print("")
    print("=" * 52)
    print("  PowerWizard Web Dashboard")
    print("=" * 52)
    print("  Mode    : {}".format(PW_MODE.upper()))
    print("  Port    : {}".format(PW_PORT))
    print("  URL     : http://localhost:{}/".format(PW_PORT))
    if PW_MODE == "real":
        if _poll_manager is not None:
            print("  Polling : background  fast=5s  slow=30s")
            print("  Backend : OK")
        else:
            print("  Backend : ERROR — not initialized")
    else:
        print("  Backend : demo (no hardware needed)")
    print("=" * 52)
    print("  Press Ctrl+C to stop")
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[PowerWizard] Stopped.")
        server.shutdown()


if __name__ == "__main__":
    run()
