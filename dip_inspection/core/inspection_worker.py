"""Inspection worker process; sole owner of the camera and inference models."""
import contextlib
import logging
import os
import queue
import signal
import socket
import threading
import time
import uuid
from pathlib import Path


class SafetyStop(RuntimeError):
    pass


class Cycle:
    """Store results for one part only; never reuse them after recovery."""
    def __init__(self):
        self.id = str(uuid.uuid4())
        self.ocr = self.side_a = self.side_b = self.product = None
        self.failed = False

    def destination(self, reject=5):
        """Approve only recognized products with two normal side results and no cycle failure."""
        approved = (not self.failed and self.ocr is not None and self.ocr["identified"]
                    and self.product is not None and self.side_a is not None and self.side_b is not None
                    and not self.side_a["anomaly"] and not self.side_b["anomaly"])
        return (self.product["destination"] if approved else reject), bool(approved)

    def history(self, destination, approved):
        best = (self.ocr or {}).get("best") or {}
        return {"cycle_id": self.id, "timestamp": time.time(),
                "part_number": (self.ocr or {}).get("part_number"),
                "ocr_status": (self.ocr or {}).get("status", "ERROR"),
                "ocr_confidence": best.get("ocr_confidence"), "pn_score": best.get("pn_score"),
                "side_a_status": (self.side_a or {}).get("status", "ERROR"),
                "side_a_score": (self.side_a or {}).get("score"),
                "side_a_threshold": (self.side_a or {}).get("threshold", 380.0),
                "side_b_status": (self.side_b or {}).get("status", "ERROR"),
                "side_b_score": (self.side_b or {}).get("score"),
                "side_b_threshold": (self.side_b or {}).get("threshold", 402.0),
                "final_result": "APROVADO" if approved else "REFUGO", "destination": destination}


@contextlib.contextmanager
def offline_initialization():
    # RapidOCR may download missing models; initialization must remain local.
    """Temporarily block socket connections during OCR initialization."""
    original = socket.socket.connect
    def denied(*args, **kwargs):
        raise RuntimeError("Modelo OCR local ausente: download bloqueado. Prepare o cache offline no UNO Q.")
    socket.socket.connect = denied
    try:
        yield
    finally:
        socket.socket.connect = original


class CameraFeed:
    """Share camera preview frames while prioritizing inspection captures."""
    def __init__(self, camera, stop, shared, length, preview_lock, emit, fps):
        self.camera, self.stop, self.shared = camera, stop, shared
        self.length, self.preview_lock, self.emit = length, preview_lock, emit
        self.lock = threading.Lock()
        self.priority = threading.Event()
        self.fps = fps
        self.thread = None
        self.failed = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        import cv2
        while not self.stop.is_set():
            if self.priority.is_set():
                self.stop.wait(0.02)
                continue
            try:
                with self.lock:
                    if self.priority.is_set():
                        continue
                    frame = self.camera.get_frame()
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    payload = encoded.tobytes()
                    if len(payload) <= len(self.shared) and self.preview_lock.acquire(timeout=0.05):
                        try:
                            self.shared[:len(payload)] = payload
                            self.length.value = len(payload)
                        finally:
                            self.preview_lock.release()
                    self.emit("update", {"last_camera_frame": time.time(), "health": {"camera": "ONLINE"}})
            except Exception:
                self.failed = RuntimeError("Falha na captura do preview.")
                self.emit("update", {"health": {"camera": "OFFLINE"}})
                return
            self.stop.wait(1 / self.fps)

    def capture(self):
        self.priority.set()
        try:
            with self.lock:
                return self.camera.capture()
        finally:
            self.priority.clear()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Captura travada; supervisor encerrará o processo.")
        with self.lock:
            self.camera.close()


def worker_main(root, settings, events, controls, stop, shared, length, preview_lock):
    # Include .eim subprocesses in the process group for forced-shutdown cleanup.
    """Run the RTDE-driven inspection loop and publish events to the supervisor."""
    if hasattr(os, "setsid"):
        os.setsid()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    os.chdir(root)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import sys
    class LogOutput:
        def __init__(self):
            self.buffer = ""
        def write(self, value):
            self.buffer += value
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line.strip():
                    emit("log", line[:1500])
            return len(value)
        def flush(self):
            pass
    def emit(kind, data):
        try:
            events.put({"kind": kind, "data": data}, timeout=0.25)
        except queue.Full:
            if kind in ("result", "fault", "safety"):
                raise RuntimeError("Supervisor não está recebendo eventos críticos.")
    # Only this process forwards component output to the supervisor.
    sys.stdout = sys.stderr = LogOutput()
    logging.getLogger().handlers.clear()
    class EventHandler(logging.Handler):
        def emit(self, record):
            emit("log", self.format(record)[:1500])
    logging.getLogger().addHandler(EventHandler())
    camera = feed = inspector = ur = telemetry = None
    feed_stop = threading.Event()
    cycle = None
    processing = False
    synchronized = False
    pending_reload = False
    safety = False
    last_health = 0

    def update(**fields):
        emit("update", fields)

    def receive():
        ur.receive()
        update(last_rtde_packet=time.time(), health={"rtde": "ONLINE"})
        mode = ur.get_safety_mode()
        if mode not in (1, 2):
            raise SafetyStop("Condição de segurança do UR: intervenção local e reinício manual necessários.")

    def response(value):
        if not ur.send_response(value):
            raise ConnectionError("Falha ao enviar resposta RTDE.")

    def destination(value):
        """Approve only recognized products with two normal side results and no cycle failure."""
        if not ur.send_destination(value):
            raise ConnectionError("Falha ao enviar destino RTDE.")

    def valid_request(command, started):
        if stop.is_set():
            return False
        # Read a fresh packet and discard results if the UR request has expired.
        receive()
        if ur.get_command() != command or time.monotonic() - started >= 9.0:
            raise RuntimeError("Pedido expirado/alterado; resultado descartado. Reinicie o ciclo localmente.")
        return True

    try:
        update(station_status="INITIALIZING", health={"worker": "RUNNING"})
        from dip_inspection.vision.camera_manager import CameraManager
        from dip_inspection.vision.ocr_processor import OCRProcessor
        from dip_inspection.vision.presence_detector import PresenceDetector
        from dip_inspection.core.product_manager import ProductManager
        from dip_inspection.vision.vision_inspector import VisionInspector
        from dip_inspection.integrations.ur_bridge import URBridge
        from dip_inspection.integrations.telemetry_publisher import TelemetryPublisher
        if not Path("empty_reference.jpg").is_file():
            raise RuntimeError("empty_reference.jpg ausente; copie a referência real do trilho vazio.")
        if not all(Path(p).is_file() for p in ("models/side_a.eim", "models/side_b.eim")):
            raise RuntimeError("Modelos .eim ausentes; pendente de validação no hardware real.")
        products = ProductManager("products.json")
        detector = PresenceDetector(reference_path="empty_reference.jpg")
        update(health={"presence": "READY"})
        with offline_initialization():
            ocr = OCRProcessor()
        update(health={"ocr": "READY"})
        inspector = VisionInspector()
        # Apply configured thresholds without changing models or preprocessing.
        inspector.SIDE_A_THRESHOLD = settings["vision"]["side_a_threshold"]
        inspector.SIDE_B_THRESHOLD = settings["vision"]["side_b_threshold"]
        update(health={"side_a": "READY", "side_b": "READY"},
               side_a_threshold=inspector.SIDE_A_THRESHOLD,
               side_b_threshold=inspector.SIDE_B_THRESHOLD)
        camera = CameraManager(device=settings["camera"]["device"], camera_name=settings["camera"]["camera_name"])
        camera.connect()
        feed = CameraFeed(camera, feed_stop, shared, length, preview_lock, emit, settings["camera"]["preview_fps"])
        feed.start()
        robot = settings["robot"]
        ur = URBridge(robot["host"], robot["port"], robot["frequency"])
        ur.connect()
        telemetry = TelemetryPublisher(**settings["mqtt"])
        telemetry.connect()
        update(station_status="WAITING_ROBOT")
        while not stop.is_set():
            emit("heartbeat", time.monotonic())
            try:
                control = controls.get_nowait()
            except queue.Empty:
                control = None
            if control == "reload_products":
                pending_reload = True
            if control == "reconnect_mqtt":
                telemetry.close()
                telemetry = TelemetryPublisher(**settings["mqtt"])
                telemetry.connect()
            if feed.failed:
                raise feed.failed
            receive()
            telemetry.publish(ur)
            if time.monotonic() - last_health > 1:
                update(last_mqtt_publish=telemetry.last_publish,
                       health={"mqtt": "ONLINE" if telemetry.connected else "OFFLINE"})
                last_health = time.monotonic()
            command = ur.get_command()
            if not synchronized:
                if command == ur.CMD_IDLE:
                    synchronized = True
                    update(station_status="WAITING_PART")
                continue
            if command == ur.CMD_IDLE:
                if processing:
                    response(ur.RESP_IDLE)
                    processing = False
                if pending_reload and cycle is None:
                    products.load()
                    pending_reload = False
                    emit("log", "Produtos recarregados entre ciclos.")
                continue
            if processing:
                continue
            processing = True
            started = time.monotonic()
            try:
                if command == ur.CMD_PRESENCE:
                    update(station_status="WAITING_PART")
                    result = detector.detect(feed.capture())
                    update(piece_present=result["present"])
                    if valid_request(command, started):
                        response(ur.RESP_OK if result["present"] else ur.RESP_NOK)
                elif command == ur.CMD_OCR:
                    if pending_reload:
                        products.load()
                        pending_reload = False
                    cycle = Cycle()
                    destination(ur.DEST_NONE)
                    update(station_status="OCR", current_part_number=None, ocr_status="PROCESSANDO",
                           ocr_confidence=None, pn_score=None, side_a_status="PENDENTE", side_a_score=None,
                           side_b_status="PENDENTE", side_b_score=None, final_result=None, destination=None)
                    result = ocr.inspect(feed.capture())
                    cycle.ocr = result
                    cycle.product = products.classify(result["part_number"]) if result["identified"] else None
                    best = result["best"] or {}
                    update(current_part_number=result["part_number"], ocr_status=result["status"],
                           ocr_confidence=best.get("ocr_confidence"), pn_score=best.get("pn_score"))
                    if valid_request(command, started):
                        response(ur.RESP_OK if cycle.product else ur.RESP_NOK)
                elif command in (ur.CMD_SIDE_A, ur.CMD_SIDE_B):
                    if cycle is None or cycle.ocr is None or (command == ur.CMD_SIDE_B and cycle.side_a is None):
                        destination(products.get_reject_destination())
                        response(ur.RESP_ERROR)
                        update(station_status="ERROR", last_error="Etapa fora de sequência; execute novo ciclo a partir do OCR.")
                        continue
                    side = "a" if command == ur.CMD_SIDE_A else "b"
                    update(station_status="INSPECTING_SIDE_" + side.upper())
                    result = getattr(inspector, "inspect_side_" + side)(feed.capture())
                    setattr(cycle, "side_" + side, result)
                    update(**{"side_" + side + "_status": result["status"], "side_" + side + "_score": result["score"],
                              "side_" + side + "_threshold": result["threshold"]})
                    if not valid_request(command, started):
                        break
                    if side == "b":
                        update(station_status="CLASSIFYING")
                        dest, approved = cycle.destination(products.get_reject_destination())
                        destination(dest)
                        response(ur.RESP_OK if not result["anomaly"] else ur.RESP_NOK)
                        update(final_result="APROVADO" if approved else "REFUGO", destination=dest,
                               station_status="WAITING_ROBOT")
                        emit("result", cycle.history(dest, approved))
                        cycle = None
                    else:
                        response(ur.RESP_OK if not result["anomaly"] else ur.RESP_NOK)
                else:
                    destination(products.get_reject_destination())
                    response(ur.RESP_ERROR)
                    if cycle:
                        cycle.failed = True
                    update(last_error="Comando RTDE desconhecido.")
            except SafetyStop:
                raise
            except Exception:
                if cycle:
                    cycle.failed = True
                update(station_status="ERROR", last_error="Falha/timeout na inspeção; peça invalidada.")
                # Never replace a reject destination with an approved one after failure.
                try:
                    destination(products.get_reject_destination())
                    response(ur.RESP_ERROR)
                except Exception:
                    pass
                raise
    except SafetyStop as error:
        safety = True
        emit("safety", str(error))
    except Exception as error:
        # Third-party messages may contain credentials; redact the configured MQTT password.
        message = "Falha no worker: " + type(error).__name__ + ": " + str(error)
        password = settings["mqtt"].get("password", "")
        if password:
            message = message.replace(password, "[redigido]")
        emit("fault", message)
    finally:
        feed_stop.set()
        for name, cleanup in (("MQTT", telemetry.close if telemetry else None),
                              ("RTDE", ur.close if ur else None),
                              ("modelos", inspector.stop if inspector else None),
                              ("câmera", feed.close if feed else camera.close if camera else None)):
            if cleanup:
                try:
                    cleanup()
                except Exception:
                    emit("log", "Falha ao liberar " + name)
        emit("exit", {"safety": safety, "requested": stop.is_set()})
