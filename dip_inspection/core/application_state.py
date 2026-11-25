"""Central supervisor state and persistent production history."""
import copy
import math
import threading
import time
from dip_inspection.core.configuration import atomic_json, load_json


def validate_production(data):
    if not isinstance(data, dict):
        raise ValueError("Produção inválida.")
    for name in ("total_processed", "total_approved", "total_rejected"):
        if type(data.get(name)) is not int or data[name] < 0:
            raise ValueError("Contador inválido.")
    if data["total_processed"] != data["total_approved"] + data["total_rejected"]:
        raise ValueError("Contadores inconsistentes.")
    counts = data.get("approved_count_by_part_number")
    if not isinstance(counts, dict) or any(type(v) is not int or v < 0 for v in counts.values()):
        raise ValueError("Contagem por produto inválida.")
    if sum(counts.values()) != data["total_approved"]:
        raise ValueError("Contagem por produto inconsistente.")
    history = data.get("recent_history")
    ids = data.get("recent_cycle_ids")
    if not isinstance(history, list) or len(history) > 100 or not isinstance(ids, list) or len(ids) > 100:
        raise ValueError("Histórico inválido.")
    for item in history:
        if not isinstance(item, dict) or not isinstance(item.get("cycle_id"), str):
            raise ValueError("Registro de inspeção inválido.")
        stamp = item.get("timestamp")
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
            raise ValueError("Timestamp inválido.")
        if item.get("final_result") not in ("APROVADO", "REFUGO"):
            raise ValueError("Resultado inválido.")
        if type(item.get("destination")) is not int or not 1 <= item["destination"] <= 5:
            raise ValueError("Destino inválido.")
        if item["final_result"] == "APROVADO" and (not isinstance(item.get("part_number"), str) or item["destination"] == 5):
            raise ValueError("Aprovação inconsistente.")
    if any(not isinstance(item, str) for item in ids):
        raise ValueError("Identificador de ciclo inválido.")
    if type(data.get("safety_latched", False)) is not bool:
        raise ValueError("Safety persistido inválido.")
    data.setdefault("safety_latched", False)
    return data


def empty_production():
    return {"total_processed": 0, "total_approved": 0, "total_rejected": 0,
            "approved_count_by_part_number": {}, "recent_history": [], "recent_cycle_ids": [], "safety_latched": False}


class ApplicationState:
    def __init__(self, root):
        self.lock = threading.RLock()
        self.path = root / "production_state.json"
        self.started_at = time.time()
        self.production = empty_production()
        self.operational = {"station_status": "STOPPED", "piece_present": None,
            "current_part_number": None, "ocr_status": "PENDENTE", "ocr_confidence": None, "pn_score": None,
            "side_a_status": "PENDENTE", "side_a_score": None, "side_a_threshold": 380.0,
            "side_b_status": "PENDENTE", "side_b_score": None, "side_b_threshold": 402.0,
            "final_result": None, "destination": None, "last_error": None, "last_inspection": None,
            "last_camera_frame": None, "last_rtde_packet": None, "last_mqtt_publish": None,
            "worker_started_at": None, "restart_attempt": 0, "safety_latched": False,
            "health": {name: "OFFLINE" for name in ("worker", "camera", "rtde", "mqtt", "ocr", "side_a", "side_b", "presence")}}
        if self.path.exists() or self.path.with_name(self.path.name + ".bak").exists():
            try:
                self.production, warning = load_json(self.path, validate_production)
                self.operational["last_error"] = warning
                if self.production["recent_history"]:
                    self.operational["last_inspection"] = self.production["recent_history"][-1]["timestamp"]
            except ValueError as error:
                # Do not silently reset a corrupted state file.
                self.operational["last_error"] = str(error)
                self.production_valid = False
                return
        self.production_valid = True

    def update(self, **fields):
        with self.lock:
            if "safety_latched" in fields:
                next_state = copy.deepcopy(self.production)
                next_state["safety_latched"] = fields["safety_latched"]
                try:
                    atomic_json(self.path, next_state)
                except OSError:
                    self.production_valid = False
                    fields["last_error"] = "Falha ao persistir safety; produção bloqueada até reparo/reset."
                # Keep the lockout in memory even if persistence fails.
                self.production = next_state
            health = fields.pop("health", None)
            self.operational.update(fields)
            if health:
                self.operational["health"].update(health)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy({**self.operational, **self.production})
        result.pop("recent_cycle_ids", None)
        result["uptime_seconds"] = int(time.time() - self.started_at)
        result["reject_rate"] = (result["total_rejected"] / result["total_processed"] * 100 if result["total_processed"] else 0)
        now = time.time()
        for key, timestamp, seconds in (("camera", "last_camera_frame", 3), ("rtde", "last_rtde_packet", 3), ("mqtt", "last_mqtt_publish", 5)):
            if result["health"][key] == "ONLINE" and (not result[timestamp] or now - result[timestamp] > seconds):
                result["health"][key] = "DEGRADED"
        return result

    def record(self, item):
        with self.lock:
            if not self.production_valid:
                raise ValueError("Produção corrompida: restaure backup ou confirme reset antes de iniciar.")
            if item["cycle_id"] in self.production["recent_cycle_ids"]:
                return False
            next_state = copy.deepcopy(self.production)
            next_state["total_processed"] += 1
            if item["final_result"] == "APROVADO":
                next_state["total_approved"] += 1
                counts = next_state["approved_count_by_part_number"]
                pn = item["part_number"]
                counts[pn] = counts.get(pn, 0) + 1
            else:
                next_state["total_rejected"] += 1
            next_state["recent_history"] = (next_state["recent_history"] + [item])[-100:]
            next_state["recent_cycle_ids"] = (next_state["recent_cycle_ids"] + [item["cycle_id"]])[-100:]
            atomic_json(self.path, next_state)
            self.production = next_state
            self.operational["last_inspection"] = item["timestamp"]
            return True

    def reset(self):
        with self.lock:
            new = empty_production()
            new["safety_latched"] = self.production.get("safety_latched", False)
            atomic_json(self.path, new)
            self.production = new
            self.production_valid = True
            self.operational["last_inspection"] = None
