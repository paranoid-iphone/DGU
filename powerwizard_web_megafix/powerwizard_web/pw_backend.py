from __future__ import annotations

import socket
import struct
import time
import threading
from dataclasses import dataclass

# ─── Configuration ────────────────────────────────────────────────────────────

HOST = "10.238.0.35"
PORT = 4001
SOCKET_TIMEOUT_SEC = 3.0
INTER_REQUEST_DELAY_SEC = 0.15

DEVICES = [
    {"name": "DG-1", "host": HOST, "port": PORT, "slave_id": 1},
    {"name": "DG-2", "host": HOST, "port": PORT, "slave_id": 2},
]

FUEL_THRESHOLDS = {
    "warning_percent": 30.0,
    "critical_percent": 15.0,
}

FID_WORD = 0xFFFF

# ─── Register map ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RegisterDef:
    key: str
    title: str
    register: int
    address: int
    words: int
    data_type: str
    scale: float
    offset: float
    unit: str
    required: bool = True
    note: str = ""


REGISTER_MAP = [
    RegisterDef("battery_voltage",    "Напряжение АКБ",                   202, 201, 1, "uint16", 0.05,       0.0,    "V"),
    RegisterDef("gen_avg_frequency",  "Частота генератора",                102, 101, 1, "uint16", 1/128,      0.0,    "Hz"),
    RegisterDef("gen_voltage",        "Напряжение генератора",             100,  99, 1, "uint16", 1.0,        0.0,    "V"),
    RegisterDef("coolant_temp",       "Температура охлаждающей жидкости", 201, 200, 1, "uint16", 0.03125, -273.0,    "°C"),
    RegisterDef("engine_oil_pressure","Давление масла",                   200, 199, 1, "uint16", 0.125,      0.0,    "kPa"),
    RegisterDef("engine_rpm",         "ЧВД",                              203, 202, 1, "uint16", 0.125,      0.0,    "rpm",  False, "Адрес тестовый — требует подтверждения"),
    RegisterDef("total_percent_kw",   "Нагрузка %",                       105, 104, 1, "uint16", 0.0078125, -251.0,  "%",    False, "Адрес тестовый — требует подтверждения"),
    RegisterDef("fuel_level",         "Уровень топлива",                  804, 803, 1, "uint16", 0.0078125, -251.0,  "%"),
    RegisterDef("engine_oil_level",   "Уровень масла",                    806, 805, 1, "uint16", 0.0078125, -251.0,  "%",  False),
    RegisterDef("fuel_consumption",   "Расход топлива",                   256, 255, 1, "uint16", 0.05,       0.0,    "L/h",False),
    RegisterDef("energy_kwh",         "Электроэнергия",                   144, 143, 2, "uint32", 1.0,        0.0,    "kWh"),
    RegisterDef("energy_kvarh",       "Реактивная энергия",               146, 145, 2, "uint32", 1.0,        0.0,    "kVArh"),
    RegisterDef("engine_hours",       "Моточасы двигателя",               204, 203, 2, "uint32", 0.05,       0.0,    "h"),
]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def bytes_to_hex(data):
    if data is None:
        return ""
    return " ".join("{:02X}".format(b) for b in data)


def build_request(slave_id, address, count):
    pdu = struct.pack(">BBHH", slave_id, 0x03, address, count)
    crc = crc16_modbus(pdu)
    return pdu + struct.pack("<H", crc)


def recv_exact(sock, size):
    chunks = []
    received = 0
    while received < size:
        chunk = sock.recv(size - received)
        if not chunk:
            raise ConnectionError("Socket closed by remote side")
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


def decode_registers(raw_words, reg):
    if reg.data_type == "uint16":
        raw_value = raw_words[0]
    elif reg.data_type == "uint32":
        raw_value = (raw_words[0] << 16) | raw_words[1]
    else:
        raise ValueError("Unsupported data_type: {}".format(reg.data_type))
    return raw_value * reg.scale + reg.offset


def is_fid(raw_words):
    return all(word == FID_WORD for word in raw_words)


def get_fuel_status(fuel_level):
    if fuel_level <= FUEL_THRESHOLDS["critical_percent"]:
        return "critical"
    if fuel_level <= FUEL_THRESHOLDS["warning_percent"]:
        return "warning"
    return "normal"

# ─── Single-register read (one TCP connection per request) ────────────────────

def read_one_register(host, port, slave_id, reg):
    """Open a fresh TCP connection, read one register group, close. Returns (raw_words, error_str)."""
    try:
        with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SEC) as sock:
            sock.settimeout(SOCKET_TIMEOUT_SEC)
            request = build_request(slave_id, reg.address, reg.words)
            sock.sendall(request)

            header = recv_exact(sock, 3)
            rx_slave = header[0]
            func = header[1]

            if rx_slave != slave_id:
                raise ValueError(
                    "Unexpected slave id: got {}, expected {}".format(rx_slave, slave_id)
                )

            if func & 0x80:
                exception_code = header[2]
                recv_exact(sock, 2)  # CRC
                raise RuntimeError(
                    "Modbus exception: func=0x{:02X}, code=0x{:02X}".format(func, exception_code)
                )

            byte_count = header[2]
            expected = reg.words * 2
            if byte_count != expected:
                recv_exact(sock, byte_count + 2)
                raise ValueError(
                    "Unexpected byte count: got {}, expected {}".format(byte_count, expected)
                )

            payload = recv_exact(sock, byte_count)
            crc_bytes = recv_exact(sock, 2)

            rx_crc = struct.unpack("<H", crc_bytes)[0]
            calc_crc = crc16_modbus(header + payload)
            if rx_crc != calc_crc:
                raise ValueError(
                    "CRC mismatch: got 0x{:04X}, expected 0x{:04X}".format(rx_crc, calc_crc)
                )

            raw_words = list(struct.unpack(">" + "H" * reg.words, payload))
            return raw_words, None

    except Exception as e:
        return None, "{}: {}".format(type(e).__name__, e)

# ─── Device poll ──────────────────────────────────────────────────────────────

def poll_device(device):
    host = device["host"]
    port = device["port"]
    slave_id = device["slave_id"]
    name = device["name"]

    print("\n=== {} | {}:{} | slave_id={} ===".format(name, host, port, slave_id))
    results = {}
    ok_any = False

    # Quick connectivity check
    try:
        with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SEC):
            pass
    except Exception as e:
        print("[CONNECT ERROR] {}:{} slave_id={} -> {}: {}".format(
            host, port, slave_id, type(e).__name__, e))
        return {
            "device": device,
            "results": results,
            "connection_status": "connect_error",
            "connect_error": "{}: {}".format(type(e).__name__, e),
        }, False

    for reg in REGISTER_MAP:
        raw_words, err = read_one_register(host, port, slave_id, reg)

        if err is not None:
            results[reg.key] = {
                "title": reg.title,
                "value": None,
                "unit": reg.unit,
                "status": "error",
                "error": err,
            }
            print("[ERR] {:<22} reg={:<4} {}".format(reg.key, reg.register, err))
        elif is_fid(raw_words):
            results[reg.key] = {
                "title": reg.title,
                "value": None,
                "unit": reg.unit,
                "status": "fid",
            }
            print("[FID] {:<22} reg={:<4} not available".format(reg.key, reg.register))
        else:
            value = decode_registers(raw_words, reg)
            entry = {
                "title": reg.title,
                "value": round(value, 3),
                "unit": reg.unit,
                "status": "ok",
            }
            if reg.key == "fuel_level":
                entry["fuel_status"] = get_fuel_status(value)
            results[reg.key] = entry
            ok_any = True
            print("[OK]  {:<22} reg={:<4} => {:.3f} {}".format(
                reg.key, reg.register, value, reg.unit))

        time.sleep(INTER_REQUEST_DELAY_SEC)

    return {
        "device": device,
        "results": results,
        "connection_status": "ok",
    }, ok_any


# ─── Public API used by main.py ───────────────────────────────────────────────

_poll_lock = threading.Lock()


def poll_once():
    """Poll all devices sequentially. Returns snapshot dict."""
    with _poll_lock:
        device_results = []
        successful = 0

        for device in DEVICES:
            report, ok_any = poll_device(device)
            device_results.append(report)
            if ok_any:
                successful += 1

        devices_dict = {}
        for report in device_results:
            d = report["device"]
            name = d["name"]
            results = report["results"]

            ok_count  = sum(1 for v in results.values() if v.get("status") == "ok")
            fid_count = sum(1 for v in results.values() if v.get("status") == "fid")
            err_count = sum(1 for v in results.values() if v.get("status") not in ("ok", "fid"))

            entry = {
                "name": name,
                "slave_id": d["slave_id"],
                "connection_status": report.get("connection_status", "ok"),
                "parameters": results,
                "summary": {
                    "ok":     ok_count,
                    "fid":    fid_count,
                    "errors": err_count,
                },
            }
            if "connect_error" in report:
                entry["connect_error"] = report["connect_error"]

            devices_dict[name] = entry

        return {
            "devices": devices_dict,
            "summary": {
                "successful_devices": successful,
                "total_devices": len(DEVICES),
            },
            "mode": "real",
        }


if __name__ == "__main__":
    import json
    result = poll_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))
