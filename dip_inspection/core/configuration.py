"""Configuration validation and atomic JSON persistence."""
import copy
import ipaddress
import json
import math
import os
from pathlib import Path
import tempfile
import threading

DEFAULTS = {
    "robot": {"host": "192.168.15.51", "port": 30004, "frequency": 10},
    "mqtt": {"host": "localhost", "port": 1883, "username": "", "password": "", "topic_prefix": "futurecom/ur"},
    "application": {"auto_start_worker": False, "auto_recovery": True,
                    "restart_limit": 3, "restart_window_seconds": 300,
                    "worker_timeout_seconds": 45},
    "camera": {"device": None, "camera_name": "USB 2.0 Camera", "preview_fps": 5},
    "vision": {"side_a_threshold": 380.0, "side_b_threshold": 402.0},
}


def atomic_json(path, data, backup=True, mode=0o600):
    """Write JSON atomically and optionally retain a readable backup."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode()
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if backup and path.exists():
            # Back up only readable JSON; never replace a backup with corrupted data.
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                previous = None
            if previous is not None:
                atomic_json(str(path) + ".bak", previous, backup=False, mode=mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path, validator):
    """Load and validate the main JSON file, falling back to its backup."""
    errors = []
    for candidate in (Path(path), Path(str(path) + ".bak")):
        try:
            result = validator(json.loads(candidate.read_text(encoding="utf-8")))
            return result, ("Arquivo principal inválido; usando backup." if errors else None)
        except (OSError, ValueError, TypeError, KeyError) as error:
            errors.append(type(error).__name__)
    raise ValueError(f"Configuração inválida ou ausente: {Path(path).name}")


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}: inteiro entre {low} e {high}.")
    return value


def validate_products(data):
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        raise ValueError("Configuração deve conter uma lista de produtos.")
    if data.get("reject_destination", 5) != 5 or type(data.get("reject_destination", 5)) is not int:
        raise ValueError("O protocolo validado reserva o destino 5 para refugo.")
    seen, boxes, products = set(), set(), []
    for item in data["products"]:
        if not isinstance(item, dict):
            raise ValueError("Produto inválido.")
        pn = item.get("part_number", "")
        if not isinstance(pn, str):
            raise ValueError("Part Number inválido.")
        pn = pn.strip().upper()
        if not 1 <= len(pn) <= 64 or any(not (c.isascii() and (c.isalnum() or c == "-")) for c in pn):
            raise ValueError("PN deve conter apenas letras, números ou hífen (até 64 caracteres).")
        if pn in seen:
            raise ValueError("Part Number duplicado.")
        seen.add(pn)
        box = integer(item.get("destination"), 1, 4, "Caixa")
        enabled = item.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("Habilitação deve ser booleana.")
        if enabled and box in boxes:
            raise ValueError("Dois produtos ativos não podem usar a mesma caixa.")
        if enabled:
            boxes.add(box)
        products.append({"part_number": pn, "destination": box, "enabled": enabled})
    return {"reject_destination": 5, "products": products}


def validate_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Configurações inválidas.")
    result = copy.deepcopy(DEFAULTS)
    for section, fields in data.items():
        if section not in result or not isinstance(fields, dict) or set(fields) - set(result[section]):
            raise ValueError("Campo de configuração desconhecido.")
        result[section].update(fields)
    ipaddress.ip_address(result["robot"]["host"])
    integer(result["robot"]["port"], 1, 65535, "Porta RTDE")
    integer(result["robot"]["frequency"], 1, 125, "Frequência RTDE")
    mqtt = result["mqtt"]
    for name in ("host", "username", "password", "topic_prefix"):
        if not isinstance(mqtt[name], str) or len(mqtt[name]) > 512 or any(c in mqtt[name] for c in "\r\n\x00"):
            raise ValueError("Campo MQTT inválido.")
    if not mqtt["host"] or not mqtt["topic_prefix"] or any(c in mqtt["topic_prefix"] for c in "+#"):
        raise ValueError("Host ou prefixo MQTT inválido.")
    integer(mqtt["port"], 1, 65535, "Porta MQTT")
    app = result["application"]
    for name in ("auto_start_worker", "auto_recovery"):
        if type(app[name]) is not bool:
            raise ValueError("Opção de aplicação deve ser booleana.")
    integer(app["restart_limit"], 1, 10, "Limite de reinícios")
    integer(app["restart_window_seconds"], 30, 3600, "Janela de reinícios")
    integer(app["worker_timeout_seconds"], 20, 300, "Timeout do worker")
    camera = result["camera"]
    device = camera["device"]
    if device is not None and (not isinstance(device, str) or not device.startswith("/dev/video") or not device[10:].isdigit()):
        raise ValueError("Dispositivo deve ser /dev/videoN ou vazio.")
    if not isinstance(camera["camera_name"], str) or not camera["camera_name"]:
        raise ValueError("Nome da câmera inválido.")
    integer(camera["preview_fps"], 1, 15, "FPS do preview")
    for name in ("side_a_threshold", "side_b_threshold"):
        value = result["vision"][name]
        if type(value) not in (int, float):
            raise ValueError("Thresholds A/B devem ser números finitos maiores ou iguais a zero.")
        try:
            value = float(value)
        except OverflowError:
            raise ValueError("Thresholds A/B devem ser números finitos maiores ou iguais a zero.")
        if not math.isfinite(value) or value < 0:
            raise ValueError("Thresholds A/B devem ser números finitos maiores ou iguais a zero.")
        result["vision"][name] = value
    return result


class Configuration:
    """Manage thread-safe settings and product configuration."""
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.errors = []
        self.products = None
        try:
            self.products, warning = load_json(self.root / "products.json", validate_products)
            if warning:
                self.errors.append("products.json: " + warning)
        except ValueError as error:
            self.errors.append(str(error))
        settings_path = self.root / "settings.json"
        if not settings_path.exists() and not Path(str(settings_path) + ".bak").exists():
            atomic_json(settings_path, DEFAULTS)
        try:
            self.settings, warning = load_json(settings_path, validate_settings)
            self.settings_valid = True
            if warning:
                self.errors.append("settings.json: " + warning)
        except ValueError as error:
            self.settings = copy.deepcopy(DEFAULTS)
            self.settings_valid = False
            self.errors.append(str(error))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.settings)

    def product_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.products)

    def save_products(self, data):
        valid = validate_products(data)
        with self.lock:
            atomic_json(self.root / "products.json", valid)
            self.products = valid
        return valid

    def save_settings(self, data):
        with self.lock:
            merged = self.snapshot()
            for section, fields in data.items():
                if section not in merged or not isinstance(fields, dict):
                    raise ValueError("Seção inválida.")
                merged[section].update(fields)
            valid = validate_settings(merged)
            atomic_json(self.root / "settings.json", valid)
            self.settings = valid
            self.settings_valid = True
        return valid
