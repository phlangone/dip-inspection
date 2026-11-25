"""Local Flask application and worker supervisor; inspection resources live in the child process."""
import collections
import copy
import hmac
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing as mp
import os
from pathlib import Path
import queue
import secrets
import signal
import threading
import time

from flask import Flask, Response, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.serving import make_server
from dip_inspection.core.application_state import ApplicationState
from dip_inspection.core.configuration import Configuration
from dip_inspection.core.inspection_worker import worker_main
from dip_inspection.integrations.wifi_manager import WifiManager


class Supervisor:
    """Manage worker lifecycle, health, recovery, logs, and shared preview."""
    def __init__(self, root, worker_target=worker_main):
        self.root = Path(root).resolve()
        self.config = Configuration(self.root)
        self.state = ApplicationState(self.root)
        self.wifi = WifiManager(lambda: self.config.snapshot()["robot"]["host"])
        self.ctx = mp.get_context("spawn")
        self.target = worker_target
        self.lock = threading.RLock()
        self.event_lock = threading.RLock()
        self.proc = None
        self.channels = None
        self.shutdown_event = threading.Event()
        self.intentional = True
        self.attempts = collections.deque()
        self.next_restart = None
        self.fault = None
        self.heartbeat = None
        self.started = None
        self.last_frame = None
        self.recovery_blocked = False
        self.group_safe = True
        self.log_lines = collections.deque(maxlen=100)
        self.logger = logging.getLogger("futurecom." + str(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        (self.root / "logs").mkdir(exist_ok=True)
        handler = RotatingFileHandler(self.root / "logs/futurecom.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(handler)
        if self.config.errors:
            self.state.update(last_error="; ".join(self.config.errors))
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

    def log(self, message):
        # Redact MQTT credentials from third-party output.
        password = self.config.snapshot()["mqtt"]["password"]
        message = str(message)
        if password:
            message = message.replace(password, "[redigido]")
        message = message[:2000]
        self.log_lines.append(message)
        self.logger.info(message)

    def running(self):
        return self.proc is not None and self.proc.is_alive()

    def start(self, manual=True):
        with self.lock:
            if self.running():
                return
            if not self.group_safe:
                raise ValueError("Processo auxiliar não foi liberado; intervenção técnica necessária.")
            if self.config.product_snapshot() is None or not self.config.settings_valid:
                raise ValueError("Configuração inválida; corrija antes de iniciar.")
            if not self.state.production_valid:
                raise ValueError("Produção inválida; restaure backup ou confirme reset.")
            if self.state.snapshot()["safety_latched"]:
                raise ValueError("Safety detectado: resolva localmente e confirme a liberação manual no painel.")
            if manual:
                self.attempts.clear()
                self.recovery_blocked = False
            self.next_restart = None
            self.intentional = False
            self.fault = None
            # Create fresh IPC for each generation; do not reuse locks or queues after forced termination.
            events = self.ctx.Queue(maxsize=256)
            controls = self.ctx.Queue(maxsize=8)
            stop = self.ctx.Event()
            shared = self.ctx.RawArray("B", 512 * 1024)
            length = self.ctx.RawValue("i", 0)
            preview_lock = self.ctx.Lock()
            self.channels = (events, controls, stop, shared, length, preview_lock)
            self.proc = self.ctx.Process(target=self.target, args=(str(self.root), self.config.snapshot(), *self.channels), name="futurecom-inspection")
            self.started = self.heartbeat = time.monotonic()
            self.state.update(station_status="INITIALIZING", worker_started_at=time.time(),
                              final_result=None, destination=None, piece_present=None,
                              health={name: "OFFLINE" for name in ("camera", "rtde", "mqtt", "ocr", "presence", "side_a", "side_b")})
            self.state.update(health={"worker": "STARTING"})
            self.proc.start()
            self.state.update(worker_pid=self.proc.pid)
            self.log("Worker iniciado.")

    def _drain(self, channels):
        with self.event_lock:
            if channels is not self.channels:
                return
            self._drain_events(channels)

    def _drain_events(self, channels):
        events, _, _, shared, length, preview_lock = channels
        for _ in range(256):
            try:
                event = events.get_nowait()
            except (queue.Empty, OSError, ValueError):
                break
            kind, data = event["kind"], event["data"]
            if kind == "update":
                self.state.update(**data)
            elif kind == "heartbeat":
                self.heartbeat = time.monotonic()
            elif kind == "result":
                try:
                    self.state.record(data)
                except Exception:
                    self.fault = "Não foi possível persistir produção; processamento bloqueado."
                    self.recovery_blocked = True
                    channels[2].set()
                    self.state.update(last_error=self.fault, station_status="ERROR")
                    self.log(self.fault)
            elif kind == "log":
                self.log(data)
            elif kind in ("fault", "safety"):
                self.fault = data
                self.state.update(last_error=data, station_status="ERROR")
                self.log(data)
                if kind == "safety":
                    self.state.update(safety_latched=True, safety_mode="INTERVENÇÃO LOCAL")
                    self.recovery_blocked = True
                    self.next_restart = None
        if preview_lock.acquire(timeout=0.01):
            try:
                size = length.value
                if 0 < size <= len(shared):
                    self.last_frame = bytes(shared[:size])
            finally:
                preview_lock.release()

    def _kill_group(self, proc):
        # Signal only the worker-created group, never the supervisor group.
        try:
            if os.getpgid(proc.pid) == proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
            elif proc.is_alive():
                proc.kill()
        except ProcessLookupError:
            pass
        proc.join(timeout=2)

    def _cleanup_orphans(self, pid):
        # An .eim process may remain after the Python process exits.
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            self.group_safe = False
            return
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _stopped_health(self, status):
        self.state.update(station_status=status, worker_pid=None,
                          health={"worker": status, "camera": "OFFLINE", "rtde": "OFFLINE", "mqtt": "OFFLINE",
                                  "ocr": "OFFLINE", "side_a": "OFFLINE", "side_b": "OFFLINE", "presence": "OFFLINE"})

    def stop(self):
        with self.lock:
            self.intentional = True
            self.next_restart = None
            proc, channels = self.proc, self.channels
            if proc:
                if channels:
                    channels[2].set()
                # Keep consuming events so the child queue can flush.
                proc.join(timeout=5)
                if proc.is_alive():
                    self.log("Worker não encerrou em 5 segundos; término forçado.")
                    self._kill_group(proc)
                if channels:
                    self._drain(channels)
                self._cleanup_orphans(proc.pid)
                self.proc = None
                self._close_channels(channels)
                self.channels = None
            self._stopped_health("STOPPED")
            self.log("Processamento parado; supervisor permanece ativo.")

    def _close_channels(self, channels):
        if channels:
            for channel in channels[:2]:
                channel.cancel_join_thread()
                channel.close()

    def restart(self):
        self.stop()
        self.start()

    def control(self, name):
        if not self.running() or not self.channels:
            raise ValueError("Processamento parado.")
        try:
            self.channels[1].put_nowait(name)
        except queue.Full:
            raise ValueError("Aguarde a operação anterior.")

    def _monitor(self):
        while not self.shutdown_event.wait(0.15):
            channels, proc = self.channels, self.proc
            if channels:
                self._drain(channels)
            if proc and proc.is_alive():
                deadline = self.config.snapshot()["application"]["worker_timeout_seconds"]
                if self.heartbeat and time.monotonic() - self.heartbeat > deadline:
                    self.fault = "Worker sem progresso; timeout de supervisão."
                    self.state.update(last_error=self.fault)
                    self.log(self.fault)
                    self._kill_group(proc)
            if proc and not proc.is_alive():
                # Lock only during generation changes; Stop may be waiting for cleanup.
                if not self.lock.acquire(blocking=False):
                    continue
                try:
                    if self.proc is not proc:
                        continue
                    proc.join(timeout=0)
                    self._drain(channels)
                    self._cleanup_orphans(proc.pid)
                    self.proc = None
                    self._close_channels(channels)
                    self.channels = None
                    if self.intentional:
                        self._stopped_health("STOPPED")
                    else:
                        self._stopped_health("ERROR")
                        self.state.update(last_error=self.fault or f"Worker encerrou inesperadamente (código {proc.exitcode}).")
                        app = self.config.snapshot()["application"]
                        now = time.monotonic()
                        while self.attempts and now - self.attempts[0] > app["restart_window_seconds"]:
                            self.attempts.popleft()
                        if app["auto_recovery"] and not self.recovery_blocked and len(self.attempts) < app["restart_limit"]:
                            self.attempts.append(now)
                            self.next_restart = now + min(30, 2 ** len(self.attempts))
                            self.state.update(station_status="RECOVERING", restart_attempt=len(self.attempts))
                            self.log(f"Recuperação automática agendada: tentativa {len(self.attempts)}.")
                        else:
                            self.state.update(station_status="FAULT", health={"worker": "FAULT"})
                finally:
                    self.lock.release()
            if self.next_restart and time.monotonic() >= self.next_restart and not self.shutdown_event.is_set():
                try:
                    self.start(manual=False)
                except Exception:
                    self.next_restart = None
                    self.state.update(station_status="FAULT", last_error="Recuperação bloqueada; intervenção necessária.")

    def close(self):
        self.shutdown_event.set()
        self.stop()
        self.monitor.join(timeout=3)
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)


def create_app(supervisor, admin_hash=None):
    """Create the local dashboard and authenticated administrative endpoints."""
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=64 * 1024)
    admin_hash = admin_hash or os.environ.get("FUTURECOM_ADMIN_HASH", "")
    failed_logins = collections.defaultdict(collections.deque)
    auth_lock = threading.Lock()

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.before_request
    def protect():
        if request.method == "POST":
            token = request.headers.get("X-CSRF-Token", "")
            if not token or not hmac.compare_digest(token, session.get("csrf", "")):
                return jsonify(error="Sessão expirada; atualize a página."), 403
            if request.path != "/api/login" and not session.get("admin"):
                return jsonify(error="Faça login administrativo."), 401
        if request.path in ("/api/settings", "/api/system/logs", "/api/wifi") and not session.get("admin"):
            return jsonify(error="Faça login administrativo."), 401

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(OSError)
    def file_error(error):
        supervisor.log("Erro de arquivo/permissão: " + type(error).__name__)
        return jsonify(error="Não foi possível acessar/gravar o arquivo. Configuração anterior preservada."), 503

    @app.errorhandler(Exception)
    def unhandled(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        supervisor.log("Erro de API: " + type(error).__name__)
        return jsonify(error="Operação não concluída; consulte Sistema."), 500

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Envie um objeto JSON.")
        return data

    for page in ("index", "products", "settings", "system"):
        def view(page=page):
            return render_template(page + ".html", page=page, csrf_token=csrf(), admin=session.get("admin", False), admin_configured=bool(admin_hash))
        app.add_url_rule("/" if page == "index" else "/" + page, "page_" + page, view)

    @app.get("/api/session")
    def session_info():
        return jsonify(admin=bool(session.get("admin")), configured=bool(admin_hash), csrf=csrf())

    @app.post("/api/login")
    def login():
        if not admin_hash:
            return jsonify(error="Senha administrativa não configurada. Siga README no UNO Q."), 503
        now = time.monotonic()
        address = request.remote_addr or "local"
        with auth_lock:
            attempts = failed_logins[address]
            while attempts and now - attempts[0] > 60:
                attempts.popleft()
            if len(attempts) >= 5:
                return jsonify(error="Aguarde um minuto antes de tentar novamente."), 429
        password = body().get("password", "")
        if not isinstance(password, str) or len(password) > 256 or not check_password_hash(admin_hash, password):
            with auth_lock:
                attempts.append(now)
            return jsonify(error="Senha incorreta."), 401
        session.clear()
        session["admin"] = True
        session.permanent = True
        app.permanent_session_lifetime = 3600
        return jsonify(ok=True, csrf=csrf())

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True, csrf=csrf())

    @app.get("/api/state")
    @app.get("/api/system/health")
    def state():
        return jsonify(supervisor.state.snapshot())

    @app.route("/api/products", methods=["GET", "POST"])
    def products():
        if request.method == "GET":
            return jsonify(supervisor.config.product_snapshot() or {"products": [], "reject_destination": 5, "invalid": True})
        valid = supervisor.config.save_products(body())
        if supervisor.running():
            supervisor.control("reload_products")
        supervisor.log("Configuração de produtos salva; aplicação entre ciclos.")
        return jsonify(ok=True, config=valid)

    @app.route("/api/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "GET":
            result = supervisor.config.snapshot()
            result["mqtt"]["password_configured"] = bool(result["mqtt"].pop("password"))
            return jsonify(result)
        with supervisor.lock:
            if supervisor.running() or supervisor.next_restart:
                raise ValueError("Pare o processamento antes de alterar conexões/configurações.")
            data = body()
            confirmed = data.pop("confirmed", False)
            if not confirmed:
                raise ValueError("Confirme a alteração das configurações.")
            supervisor.config.save_settings(data)
        supervisor.log("Configurações salvas; serão usadas no próximo Start.")
        return jsonify(ok=True)

    @app.post("/api/system/<action>")
    def system_action(action):
        data = body()
        if action in ("stop", "restart", "reset-production", "acknowledge-safety") and data.get("confirmed") is not True:
            raise ValueError("Confirmação necessária.")
        if action == "start":
            supervisor.start()
        elif action == "stop":
            supervisor.stop()
        elif action == "restart":
            supervisor.restart()
        elif action == "reload-products":
            # Validate on read, with backup fallback; preserve in-memory configuration on failure.
            from dip_inspection.core.configuration import load_json, validate_products
            valid, warning = load_json(supervisor.root / "products.json", validate_products)
            with supervisor.config.lock:
                supervisor.config.products = valid
            if supervisor.running():
                supervisor.control("reload_products")
        elif action == "reconnect-mqtt":
            supervisor.control("reconnect_mqtt")
        elif action == "reset-production":
            with supervisor.lock:
                if supervisor.running() or supervisor.next_restart:
                    raise ValueError("Pare o processamento antes de resetar produção.")
                supervisor.state.reset()
        elif action == "acknowledge-safety":
            if supervisor.running():
                raise ValueError("Pare processamento primeiro.")
            supervisor.state.update(safety_latched=False)
            supervisor.recovery_blocked = False
            supervisor.log("Operador confirmou resolução local de safety; próximo Start verifica safety_mode novamente.")
        else:
            raise ValueError("Ação desconhecida.")
        return jsonify(ok=True)

    @app.post("/api/mqtt/test")
    def test_mqtt():
        import paho.mqtt.client as mqtt
        config = supervisor.config.snapshot()["mqtt"]
        finished = threading.Event()
        outcome = {"ok": False, "message": "Sem confirmação MQTT em 4 segundos."}
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        def on_connect(client, userdata, flags, reason, properties):
            outcome["ok"] = reason == 0
            outcome["message"] = "Broker confirmou conexão MQTT." if reason == 0 else "Broker recusou autenticação/conexão."
            finished.set()
        client.on_connect = on_connect
        if config["username"]:
            client.username_pw_set(config["username"], config["password"])
        try:
            client.connect_async(config["host"], config["port"], 10)
            client.loop_start()
            finished.wait(4)
        finally:
            client.disconnect()
            client.loop_stop()
        return jsonify(outcome)

    @app.get("/api/system/logs")
    def logs():
        return jsonify(lines=list(supervisor.log_lines))

    @app.route("/api/wifi", methods=["GET", "POST"])
    def wifi():
        if request.method == "GET":
            return jsonify(supervisor.wifi.status())
        data = body()
        action = data.get("action")
        if action == "scan":
            return jsonify(supervisor.wifi.scan())
        with supervisor.lock:
            if supervisor.running() or supervisor.next_restart:
                raise ValueError("Pare processamento antes de alterar Wi-Fi.")
            if data.get("confirmed") is not True:
                raise ValueError("Confirme que a conexão com esta página poderá ser perdida.")
            return jsonify(supervisor.wifi.change(action, data))

    @app.get("/video_feed")
    def video():
        def frames():
            while True:
                payload = supervisor.last_frame
                if payload:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
                time.sleep(0.2)
        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    app.supervisor = supervisor
    return app


def main():
    root = Path(__file__).resolve().parents[2]
    # Prevent multiple supervisors and camera owners in the same project.
    import fcntl
    lockfile = open(root / ".supervisor.lock", "w")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Supervisor já está executando neste projeto.")
    supervisor = Supervisor(root)
    app = create_app(supervisor)
    if supervisor.config.snapshot()["application"]["auto_start_worker"]:
        try:
            supervisor.start()
        except Exception as error:
            supervisor.state.update(last_error=str(error), station_status="ERROR")
    server = make_server("0.0.0.0", int(os.environ.get("FUTURECOM_PORT", "5000")), app, threaded=True)
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        supervisor.close()
        server.server_close()
        lockfile.close()


if __name__ == "__main__":
    main()
