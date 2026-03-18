"""
PollManager — background Modbus polling for PowerWizard Web Dashboard.

Architecture:
  - One EndpointWorker thread per unique (host, port)
  - Devices on same endpoint polled STRICTLY SEQUENTIALLY — safe for shared RTU line
  - Block reads: multiple registers per TCP connection → fewer round-trips
  - Fast cycle  (~5s):  operational params (voltage, freq, temp, pressure)
  - Slow cycle  (~30s): counters and levels (hours, energy, fuel)
  - SnapshotStore updated after each block → frontend gets partial data immediately

Modbus safety guarantee:
  At most ONE TCP connection to a given (host, port) is open at any time.
  No parallel slave_id requests on same gateway — eliminates "Unexpected slave id" errors.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from pw_backend import (
    crc16_modbus,
    recv_exact,
    DEVICES,
    FUEL_THRESHOLDS,
    FID_WORD,
    SOCKET_TIMEOUT_SEC,
    INTER_REQUEST_DELAY_SEC,
    get_fuel_status,
)

# ─── Timing ───────────────────────────────────────────────────────────────────

FAST_INTERVAL_SEC = 5.0   # how often to poll fast blocks (per device)
SLOW_RATIO = 6            # run slow blocks every N fast cycles ≈ 30s

# ─── Block / parameter definitions ───────────────────────────────────────────

def _bp(key, title, offset, words, dtype, scale, zero, unit, required=True):
    return dict(key=key, title=title, offset=offset, words=words,
                dtype=dtype, scale=scale, zero=zero, unit=unit, required=required)


# Fast blocks: operational / safety-critical parameters
FAST_BLOCKS = [
    # Addresses 99..104 — generator frequency and voltage
    # word[0]=gen_voltage(99), word[2]=gen_avg_frequency(101), word[5]=total_percent_kw(104)
    {
        "start": 99,
        "count": 6,
        "params": [
            _bp("gen_voltage",       "Напряжение генератора", 0, 1, "u16", 1.0,       0.0,    "V"),
            _bp("gen_avg_frequency", "Частота генератора",    2, 1, "u16", 1 / 128,   0.0,    "Hz"),
            _bp("total_percent_kw",  "Нагрузка %",            5, 1, "u16", 0.0078125, -251.0, "%",  False),
        ],
    },
    # Addresses 199..202 — pressures, temps, battery, RPM
    # word[0]=engine_oil_pressure(199), word[1]=coolant_temp(200),
    # word[2]=battery_voltage(201),     word[3]=engine_rpm(202)
    {
        "start": 199,
        "count": 4,
        "params": [
            _bp("engine_oil_pressure", "Давление масла",   0, 1, "u16", 0.125,   0.0,    "kPa"),
            _bp("coolant_temp",        "Темп. охл.жид.",   1, 1, "u16", 0.03125, -273.0, "°C"),
            _bp("battery_voltage",     "Напряжение АКБ",   2, 1, "u16", 0.05,    0.0,    "V"),
            _bp("engine_rpm",          "ЧВД",              3, 1, "u16", 0.125,   0.0,    "rpm", False),
        ],
    },
]

# Slow blocks: counters and levels (change slowly, don't need frequent reads)
SLOW_BLOCKS = [
    # Addresses 143..146 — energy counters
    # word[0:2]=energy_kwh(uint32), word[2:4]=energy_kvarh(uint32)
    {
        "start": 143,
        "count": 4,
        "params": [
            _bp("energy_kwh",   "Электроэнергия",     0, 2, "u32", 1.0, 0.0, "kWh"),
            _bp("energy_kvarh", "Реактивная энергия", 2, 2, "u32", 1.0, 0.0, "kVArh"),
        ],
    },
    # Addresses 203..204 — engine hours (uint32)
    {
        "start": 203,
        "count": 2,
        "params": [
            _bp("engine_hours", "Моточасы двигателя", 0, 2, "u32", 0.05, 0.0, "h"),
        ],
    },
    # Addresses 803..805 — fuel level (803), skip(804), engine_oil_level(805)
    {
        "start": 803,
        "count": 3,
        "params": [
            _bp("fuel_level",       "Уровень топлива", 0, 1, "u16", 0.0078125, -251.0, "%"),
            _bp("engine_oil_level", "Уровень масла",   2, 1, "u16", 0.0078125, -251.0, "%", False),
        ],
    },
    # Address 255 — fuel consumption
    {
        "start": 255,
        "count": 1,
        "params": [
            _bp("fuel_consumption", "Расход топлива", 0, 1, "u16", 0.05, 0.0, "L/h", False),
        ],
    },
]

# ─── Low-level block read (one TCP connection per block) ──────────────────────

def _build_request(slave_id, address, count):
    pdu = struct.pack(">BBHH", slave_id, 0x03, address, count)
    crc = crc16_modbus(pdu)
    return pdu + struct.pack("<H", crc)


def _read_block(host, port, slave_id, start_addr, count):
    """
    Open a fresh TCP connection, read `count` holding registers starting at
    `start_addr` for `slave_id`. Close connection. Returns (words, None) or
    (None, error_string).

    One connection per call → no shared socket state between reads.
    """
    try:
        request = _build_request(slave_id, start_addr, count)
        with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SEC) as sock:
            sock.settimeout(SOCKET_TIMEOUT_SEC)
            sock.sendall(request)

            header = recv_exact(sock, 3)
            rx_slave, func, third = header[0], header[1], header[2]

            if rx_slave != slave_id:
                return None, "Unexpected slave id: got {}, expected {}".format(
                    rx_slave, slave_id)

            if func & 0x80:
                recv_exact(sock, 2)   # discard CRC
                return None, "Modbus exception func=0x{:02X} code=0x{:02X}".format(
                    func, third)

            byte_count = third
            expected = count * 2
            if byte_count != expected:
                recv_exact(sock, byte_count + 2)
                return None, "Byte count mismatch: got {}, expected {}".format(
                    byte_count, expected)

            payload = recv_exact(sock, byte_count)
            crc_bytes = recv_exact(sock, 2)
            rx_crc = struct.unpack("<H", crc_bytes)[0]
            calc_crc = crc16_modbus(header + payload)
            if rx_crc != calc_crc:
                return None, "CRC mismatch: got 0x{:04X} expected 0x{:04X}".format(
                    rx_crc, calc_crc)

            return list(struct.unpack(">" + "H" * count, payload)), None

    except Exception as e:
        return None, "{}: {}".format(type(e).__name__, e)


# ─── Decode ───────────────────────────────────────────────────────────────────

def _decode_param(words, p):
    """Returns (float_value, 'ok') or (None, 'fid')."""
    w = words[p["offset"]: p["offset"] + p["words"]]
    if all(x == FID_WORD for x in w):
        return None, "fid"
    if p["dtype"] == "u16":
        raw = w[0]
    else:   # u32
        raw = (w[0] << 16) | w[1]
    return raw * p["scale"] + p["zero"], "ok"


def _classify_status(key, value, context=None):
    """
    Return 'ok', 'warning', or 'critical' for a given parameter.
    `context` is the already-built params dict (used for gen_avg_frequency check).
    """
    if key == "fuel_level":
        if value <= FUEL_THRESHOLDS["critical_percent"]:
            return "critical"
        if value <= FUEL_THRESHOLDS["warning_percent"]:
            return "warning"
    elif key == "coolant_temp":
        if value > 95.0:
            return "critical"
        if value > 85.0:
            return "warning"
    elif key == "battery_voltage":
        if value < 11.5:
            return "critical"
        if value < 12.0:
            return "warning"
    elif key == "total_percent_kw":
        if value > 85.0:
            return "critical"
        if value > 70.0:
            return "warning"
    elif key == "gen_avg_frequency" and context:
        rpm_entry = context.get("engine_rpm", {})
        rpm = rpm_entry.get("value") if rpm_entry else None
        if rpm is not None and rpm > 200:
            if value == 0.0:
                return "critical"
            if value < 49.0 or value > 51.0:
                return "warning"
    return "ok"


def _build_entry(p, words, context=None):
    """
    Decode one parameter from a block's words.
    Returns {key: param_dict}.
    """
    value, vstatus = _decode_param(words, p)
    if vstatus == "fid":
        return {p["key"]: {
            "title": p["title"], "value": None,
            "unit": p["unit"], "status": "fid",
        }}
    status = _classify_status(p["key"], value, context)
    entry = {
        "title": p["title"],
        "value": round(value, 3),
        "unit": p["unit"],
        "status": status,
    }
    if p["key"] == "fuel_level":
        entry["fuel_status"] = get_fuel_status(value)
    return {p["key"]: entry}


# ─── Connect-error detection ──────────────────────────────────────────────────

_CONNECT_KEYWORDS = (
    "TimeoutError", "ConnectionRefusedError", "ConnectionError",
    "ConnectionAbortedError", "OSError", "timed out", "refused",
    "No route", "Network is unreachable",
)


def _is_connect_error(err):
    return any(kw in err for kw in _CONNECT_KEYWORDS)


# ─── EndpointWorker ───────────────────────────────────────────────────────────

class EndpointWorker:
    """
    One daemon thread per (host, port) endpoint.
    Polls all slave_ids on this endpoint sequentially.

    KEY SAFETY PROPERTY:
    Only one TCP connection to (host, port) is ever open at a time.
    No concurrent Modbus requests → no "Unexpected slave id" / response mixing.
    """

    def __init__(self, host, port, devices, store):
        self._host = host
        self._port = port
        self._devices = devices   # [{"name": ..., "slave_id": ...}, ...]
        self._store = store
        self._slow_tick = 0

    def start(self):
        t = threading.Thread(
            target=self._run,
            daemon=True,
            name="pw-worker-{}:{}".format(self._host, self._port),
        )
        t.start()
        print("[WORKER] {}:{}  devices={}  fast={}s slow={}s".format(
            self._host, self._port,
            [d["name"] for d in self._devices],
            FAST_INTERVAL_SEC,
            int(FAST_INTERVAL_SEC * SLOW_RATIO),
        ))

    def _run(self):
        while True:
            t0 = time.time()

            do_slow = (self._slow_tick == 0)
            self._slow_tick = (self._slow_tick + 1) % SLOW_RATIO

            for device in self._devices:
                try:
                    self._poll_device(device["name"], device["slave_id"], do_slow)
                except Exception as e:
                    print("[WORKER] Unexpected error {} : {}".format(
                        device["name"], e))
                # Pause between devices — same RTU line
                time.sleep(INTER_REQUEST_DELAY_SEC)

            elapsed = time.time() - t0
            sleep_t = max(0.0, FAST_INTERVAL_SEC - elapsed)
            time.sleep(sleep_t)

    def _poll_device(self, name, slave_id, do_slow):
        host, port = self._host, self._port
        fast_params = {}

        # ── Fast blocks ───────────────────────────────────────────────────────
        for block in FAST_BLOCKS:
            words, err = _read_block(host, port, slave_id,
                                     block["start"], block["count"])
            if err:
                if _is_connect_error(err):
                    self._store.set_connect_error(name, err)
                    print("[WORKER] {} connect error: {}".format(name, err))
                    return
                # Protocol error on this block — mark params as error
                for p in block["params"]:
                    fast_params[p["key"]] = {
                        "title": p["title"], "value": None,
                        "unit": p["unit"], "status": "error", "error": err,
                    }
                time.sleep(INTER_REQUEST_DELAY_SEC)
                continue

            for p in block["params"]:
                fast_params.update(_build_entry(p, words, fast_params))
            time.sleep(INTER_REQUEST_DELAY_SEC)

        # Push fast params immediately → frontend gets partial update
        self._store.update_params(name, fast_params)

        ok_count = sum(1 for v in fast_params.values() if v.get("status") == "ok")
        err_count = sum(1 for v in fast_params.values() if v.get("status") == "error")
        print("[WORKER] {} fast: ok={} err={}{}".format(
            name, ok_count, err_count, " [SLOW]" if do_slow else ""))

        # ── Slow blocks (every SLOW_RATIO cycles) ─────────────────────────────
        if not do_slow:
            return

        slow_params = {}
        for block in SLOW_BLOCKS:
            words, err = _read_block(host, port, slave_id,
                                     block["start"], block["count"])
            if err:
                if _is_connect_error(err):
                    break
                for p in block["params"]:
                    slow_params[p["key"]] = {
                        "title": p["title"], "value": None,
                        "unit": p["unit"], "status": "error", "error": err,
                    }
                time.sleep(INTER_REQUEST_DELAY_SEC)
                continue

            for p in block["params"]:
                slow_params.update(_build_entry(p, words))
            time.sleep(INTER_REQUEST_DELAY_SEC)

        if slow_params:
            self._store.update_params(name, slow_params)
            print("[WORKER] {} slow: {} params".format(name, len(slow_params)))


# ─── PollManager ─────────────────────────────────────────────────────────────

class PollManager:
    """
    Groups devices by (host, port) endpoint.
    Starts one EndpointWorker per unique endpoint.

    If two DGUs share the same MOXA gateway → one worker, sequential polling.
    If two DGUs have different gateways    → two workers, parallel (safe).
    """

    def __init__(self, devices, store):
        endpoints = {}
        for d in devices:
            key = (d["host"], d["port"])
            endpoints.setdefault(key, []).append(d)

        self._workers = [
            EndpointWorker(host, port, devs, store)
            for (host, port), devs in endpoints.items()
        ]

    def start(self):
        for w in self._workers:
            w.start()
