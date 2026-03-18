"""
SnapshotStore — thread-safe in-memory store for device snapshot data.

Updated by EndpointWorker after each block read (partial updates ok).
Read by HTTP handler instantly — no waiting for polling.
"""
from __future__ import annotations

import copy
import threading
import time


class SnapshotStore:
    def __init__(self, devices, mode="real"):
        """
        devices: list of {"name": str, "slave_id": int, ...}
        """
        self._lock = threading.Lock()
        self._mode = mode
        self._backend_status = "warming_up"
        self._last_error = None
        self._first_done = set()
        self._all_names = {d["name"] for d in devices}
        self._devices = {
            d["name"]: {
                "name": d["name"],
                "slave_id": d["slave_id"],
                "connection_status": "unknown",
                "parameters": {},
            }
            for d in devices
        }

    # ── Write API (called from EndpointWorker thread) ─────────────────────────

    def update_params(self, device_name, params):
        """
        Merge `params` dict into device's parameters.
        Partial updates are fine — called after each block read.
        """
        with self._lock:
            dev = self._devices.get(device_name)
            if dev is None:
                return
            dev["parameters"].update(params)
            dev["connection_status"] = "ok"
            dev.pop("connect_error", None)
            self._mark_done(device_name)

    def set_connect_error(self, device_name, error_msg):
        with self._lock:
            dev = self._devices.get(device_name)
            if dev is None:
                return
            dev["connection_status"] = "connect_error"
            dev["connect_error"] = error_msg
            self._mark_done(device_name)

    def set_backend_error(self, error_msg):
        with self._lock:
            self._backend_status = "error"
            self._last_error = error_msg

    def _mark_done(self, device_name):
        """Track which devices have completed at least one fast cycle."""
        self._first_done.add(device_name)
        if self._first_done >= self._all_names and self._backend_status == "warming_up":
            self._backend_status = "ok"

    # ── Read API (called from HTTP handler thread) ────────────────────────────

    def get_snapshot(self):
        """Return full snapshot dict. Always instant — no I/O."""
        with self._lock:
            devices = {}
            for name, dev in self._devices.items():
                d = copy.deepcopy(dev)

                params = d.get("parameters", {})
                statuses = [p.get("status", "ok") for p in params.values()]
                conn = d.get("connection_status", "unknown")

                if conn in ("connect_error", "unknown"):
                    d["aggregate_status"] = "offline"
                elif "critical" in statuses:
                    d["aggregate_status"] = "critical"
                elif "warning" in statuses:
                    d["aggregate_status"] = "warning"
                else:
                    d["aggregate_status"] = "ok"

                d["summary"] = {
                    "ok":     sum(1 for s in statuses if s == "ok"),
                    "fid":    sum(1 for s in statuses if s == "fid"),
                    "errors": sum(1 for s in statuses if s in ("error", "critical", "warning")),
                }
                devices[name] = d

            successful = sum(
                1 for d in devices.values()
                if d.get("connection_status") == "ok"
            )

            result = {
                "timestamp": int(time.time() * 1000),
                "backend_status": self._backend_status,
                "devices": devices,
                "summary": {
                    "successful_devices": successful,
                    "total_devices": len(devices),
                },
                "mode": self._mode,
            }
            if self._last_error:
                result["error"] = self._last_error
            return result
