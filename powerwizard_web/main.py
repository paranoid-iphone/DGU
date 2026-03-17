"""
PowerWizard Web Dashboard — HTTP server (stdlib only, Python 3.7+)
Reads PW_MODE env var: "demo" or "real"
"""
from __future__ import annotations

import json
import math
import os
import random
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler

# ─── Mode ─────────────────────────────────────────────────────────────────────

PW_MODE = os.environ.get("PW_MODE", "demo")
PW_PORT = int(os.environ.get("PW_PORT", "8000"))

_backend = None
_poll_lock = threading.Lock()

if PW_MODE == "real":
    try:
        import pw_backend as _backend
        print("[INFO] Real backend loaded.")
    except ImportError as e:
        print("[WARN] pw_backend import failed: {}".format(e))
        _backend = None

# ─── Demo snapshot ────────────────────────────────────────────────────────────

_DEMO_T0 = time.time()
_DEMO_SCENARIO_TICK = 0
_DEMO_SCENARIO_LOCK = threading.Lock()


def _demo_scenario_index():
    """Slowly advancing scenario index (changes every ~30s)."""
    elapsed = time.time() - _DEMO_T0
    return int(elapsed / 30) % 6


def _make_demo_device(name, slave_id, scenario):
    """Build a single device entry for demo mode."""
    t = time.time()
    wave = math.sin(t * 0.3 + slave_id)
    wave2 = math.sin(t * 0.17 + slave_id * 2)

    # Base values
    rpm_base = 1500.0
    freq_base = 50.0
    load_base = 45.0 + wave * 15.0
    fuel = max(5.0, min(100.0, 62.0 - slave_id * 8 + wave2 * 5))
    coolant = 78.0 + wave * 8.0
    battery = 13.2 + wave2 * 0.4
    oil_pressure = 280.0 + wave * 20.0
    kwh = 3200.0 + slave_id * 900 + elapsed_h(t) * 15
    hours = 1800.0 + slave_id * 450 + elapsed_h(t)

    # Scenario overrides
    if scenario == 1 and slave_id == 1:
        # DG-1: engine running, frequency dropped
        freq_base = 47.2
    elif scenario == 2 and slave_id == 2:
        # DG-2: engine running, frequency lost
        freq_base = 0.0
    elif scenario == 3 and slave_id == 1:
        # DG-1: low fuel warning
        fuel = 22.0
    elif scenario == 4 and slave_id == 2:
        # DG-2: critical fuel
        fuel = 9.0
    elif scenario == 5:
        # Both engines stopped
        rpm_base = 0.0
        freq_base = 0.0
        load_base = 0.0

    engine_running = rpm_base > 200

    # Fuel status
    if fuel <= 15.0:
        fuel_status = "critical"
    elif fuel <= 30.0:
        fuel_status = "warning"
    else:
        fuel_status = "normal"

    # Frequency status
    if engine_running and freq_base == 0.0:
        freq_status = "critical"
    elif engine_running and (freq_base < 49.0 or freq_base > 51.0):
        freq_status = "warning"
    else:
        freq_status = "ok"

    # Coolant status
    if coolant > 95.0:
        coolant_status = "critical"
    elif coolant > 85.0:
        coolant_status = "warning"
    else:
        coolant_status = "ok"

    # Load status
    if load_base > 85.0:
        load_status = "critical"
    elif load_base > 70.0:
        load_status = "warning"
    else:
        load_status = "ok"

    # Battery status
    if battery < 11.5:
        batt_status = "critical"
    elif battery < 12.0:
        batt_status = "warning"
    else:
        batt_status = "ok"

    parameters = {
        "battery_voltage": {
            "title": "Напряжение АКБ",
            "value": round(battery, 2),
            "unit": "V",
            "status": batt_status,
        },
        "gen_avg_frequency": {
            "title": "Частота генератора",
            "value": round(freq_base + wave2 * 0.05 if freq_base > 0 else 0.0, 3),
            "unit": "Hz",
            "status": freq_status,
        },
        "gen_voltage": {
            "title": "Напряжение генератора",
            "value": round(400.0 + wave * 5 if engine_running else 0.0, 1),
            "unit": "V",
            "status": "ok",
        },
        "coolant_temp": {
            "title": "Температура охлаждающей жидкости",
            "value": round(coolant, 2),
            "unit": "°C",
            "status": coolant_status,
        },
        "engine_oil_pressure": {
            "title": "Давление масла",
            "value": round(oil_pressure if engine_running else 0.0, 1),
            "unit": "kPa",
            "status": "ok",
        },
        "engine_rpm": {
            "title": "ЧВД",
            "value": round(rpm_base + wave * 10 if engine_running else 0.0, 1),
            "unit": "rpm",
            "status": "ok",
        },
        "total_percent_kw": {
            "title": "Нагрузка %",
            "value": round(max(0.0, load_base) if engine_running else 0.0, 1),
            "unit": "%",
            "status": load_status if engine_running else "ok",
        },
        "fuel_level": {
            "title": "Уровень топлива",
            "value": round(fuel, 1),
            "unit": "%",
            "status": fuel_status if fuel_status != "normal" else "ok",
            "fuel_status": fuel_status,
        },
        "energy_kwh": {
            "title": "Электроэнергия",
            "value": round(kwh, 1),
            "unit": "kWh",
            "status": "ok",
        },
        "energy_kvarh": {
            "title": "Реактивная энергия",
            "value": round(kwh * 0.02, 1),
            "unit": "kVArh",
            "status": "ok",
        },
        "engine_hours": {
            "title": "Моточасы двигателя",
            "value": round(hours, 2),
            "unit": "h",
            "status": "ok",
        },
    }

    # Aggregate status
    all_statuses = [p.get("status", "ok") for p in parameters.values()]
    if "critical" in all_statuses:
        agg_status = "critical"
    elif "warning" in all_statuses:
        agg_status = "warning"
    else:
        agg_status = "ok"

    ok_count  = sum(1 for s in all_statuses if s == "ok")
    err_count = sum(1 for s in all_statuses if s in ("critical", "warning", "error"))

    return {
        "name": name,
        "slave_id": slave_id,
        "connection_status": "ok",
        "aggregate_status": agg_status,
        "parameters": parameters,
        "summary": {
            "ok": ok_count,
            "fid": 0,
            "errors": err_count,
        },
    }


def elapsed_h(t):
    return (t - _DEMO_T0) / 3600.0


def build_demo_snapshot():
    scenario = _demo_scenario_index()
    devices = {}
    for dev in [{"name": "DG-1", "slave_id": 1}, {"name": "DG-2", "slave_id": 2}]:
        devices[dev["name"]] = _make_demo_device(dev["name"], dev["slave_id"], scenario)

    successful = sum(1 for d in devices.values() if d["connection_status"] == "ok")
    return {
        "timestamp": int(time.time() * 1000),
        "devices": devices,
        "summary": {
            "successful_devices": successful,
            "total_devices": len(devices),
        },
        "mode": "demo",
        "demo_scenario": scenario,
    }


# ─── Real snapshot — background poller ───────────────────────────────────────

_snapshot_cache = None   # последний успешный снэпшот
_snapshot_lock  = threading.Lock()
_POLL_INTERVAL  = 15     # секунд между опросами

def _compute_aggregate(data):
    """Добавляет aggregate_status к каждому устройству."""
    for dev in data.get("devices", {}).values():
        params = dev.get("parameters", {})
        statuses = [p.get("status", "ok") for p in params.values()]
        if dev.get("connection_status") == "connect_error":
            dev["aggregate_status"] = "offline"
        elif "critical" in statuses:
            dev["aggregate_status"] = "critical"
        elif "warning" in statuses:
            dev["aggregate_status"] = "warning"
        else:
            dev["aggregate_status"] = "ok"


def _background_poll_loop():
    """Фоновый тред: опрашивает устройства и кладёт результат в кэш."""
    global _snapshot_cache
    while True:
        if _backend is not None:
            try:
                print("[POLL] polling devices...")
                with _poll_lock:
                    data = _backend.poll_once()
                data["timestamp"] = int(time.time() * 1000)

                summary = data.get("summary", {})
                print("[POLL] done: {}/{} ok".format(
                    summary.get("successful_devices", 0),
                    summary.get("total_devices", 0)
                ))
                for dname, dev in data.get("devices", {}).items():
                    s = dev.get("summary", {})
                    if dev.get("connection_status") == "connect_error":
                        print("  [{}] CONNECT ERROR: {}".format(
                            dname, dev.get("connect_error", "")))
                    else:
                        print("  [{}] ok={} fid={} errors={}".format(
                            dname, s.get("ok",0), s.get("fid",0), s.get("errors",0)))

                _compute_aggregate(data)

                with _snapshot_lock:
                    _snapshot_cache = data

            except Exception as e:
                import traceback
                print("[POLL ERROR] {}: {}".format(type(e).__name__, e))
                print(traceback.format_exc())

        time.sleep(_POLL_INTERVAL)


def get_snapshot():
    if PW_MODE == "real":
        with _snapshot_lock:
            cached = _snapshot_cache
        if cached is not None:
            # Обновляем только timestamp чтобы фронтенд видел свежий ответ
            import copy
            snap = copy.copy(cached)
            snap["timestamp"] = int(time.time() * 1000)
            snap["cache_age_s"] = round(
                (snap["timestamp"] - cached["timestamp"]) / 1000, 1
            ) if "timestamp" in cached else 0
            return snap
        # Кэш ещё пуст — идёт первый опрос
        return {
            "timestamp": int(time.time() * 1000),
            "devices": {},
            "summary": {"successful_devices": 0, "total_devices": 0},
            "mode": "real",
            "status": "warming_up",
            "message": "Первый опрос устройств, подождите...",
        }
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
        # Log only errors (4xx, 5xx), suppress 2xx/3xx noise
        if args and len(args) >= 2:
            code = str(args[1])
            if code.startswith(('4', '5')):
                print("[HTTP] {} {} {}".format(
                    self.address_string(),
                    self.requestline,
                    code
                ))

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
        import mimetypes
        import posixpath

        # Sanitize path
        path = path.split("?")[0]
        path = posixpath.normpath(path)
        if path == "/" or path == "":
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

        if path == "/api/health":
            status = "ok"
            issues = []
            if PW_MODE == "real" and _backend is None:
                status = "error"
                issues.append("pw_backend not loaded")
            self.send_json({
                "status": status,
                "mode": PW_MODE,
                "backend_loaded": _backend is not None,
                "issues": issues,
            })
            return

        if path == "/api/snapshot":
            try:
                snapshot = get_snapshot()
                self.send_json(snapshot)
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
            return

        if path == "/api/mode":
            self.send_json({"mode": PW_MODE})
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
    # Запускаем фоновый поллер только в real-режиме
    if PW_MODE == "real":
        t = threading.Thread(target=_background_poll_loop, daemon=True)
        t.start()
        print("[INFO] Background poller started (interval={}s)".format(_POLL_INTERVAL))

    server = ThreadingServer(("0.0.0.0", PW_PORT), Handler)

    # ── Startup banner ────────────────────────────────────────────
    print("")
    print("=" * 52)
    print("  PowerWizard Web Dashboard")
    print("=" * 52)
    print("  Mode    : {}".format(PW_MODE.upper()))
    print("  Port    : {}".format(PW_PORT))
    print("  URL     : http://localhost:{}/".format(PW_PORT))
    if PW_MODE == "real":
        if _backend is not None:
            print("  Backend : OK — pw_backend loaded")
        else:
            print("  Backend : ERROR — pw_backend not loaded!")
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
