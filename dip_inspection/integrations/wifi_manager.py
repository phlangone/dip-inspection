"""NetworkManager integration restricted to Wi-Fi devices; does not modify Ethernet."""
import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid


class WifiManager:
    """Manage Wi-Fi profiles while protecting the route to the robot."""
    def __init__(self, robot_host):
        self.robot_host = robot_host
        self.lock = threading.Lock()
        self.cached = {"status": "UNAVAILABLE", "interface": None, "message": "NetworkManager não verificado."}
        self.cache_time = 0
        self.last_interface = None

    def _run(self, args, timeout=15):
        if not shutil.which("nmcli"):
            raise ValueError("nmcli indisponível; instale/verifique NetworkManager no UNO Q.")
        result = subprocess.run(["nmcli", "--wait", "10", *args], capture_output=True, text=True,
                                timeout=timeout, env={**os.environ, "LC_ALL": "C"})
        if result.returncode and os.environ.get("FUTURECOM_WIFI_HELPER") == "1":
            allowed = args[:2] in (["connection", "add"], ["connection", "modify"], ["connection", "up"], ["connection", "delete"], ["device", "disconnect"]) or args[:3] == ["device", "wifi", "rescan"]
            if allowed:
                payload = json.dumps({"args": args, "robot_host": self.robot_host()})
                result = subprocess.run(["sudo", "-n", "/usr/local/libexec/futurecom-wifi"], input=payload,
                                        capture_output=True, text=True, timeout=20,
                                        env={**os.environ, "LC_ALL": "C"})
        if result.returncode:
            # nmcli may echo SSIDs and arguments; never expose raw stderr.
            raise ValueError("NetworkManager recusou a operação. Verifique conexão e permissão PolicyKit.")
        return result.stdout.strip()

    def interface(self):
        rows = self._run(["-t", "-f", "DEVICE,TYPE", "device", "status"])
        devices = [row.split(":", 1)[0] for row in rows.splitlines() if row.endswith(":wifi")]
        if not devices:
            raise ValueError("Nenhuma interface Wi-Fi detectada.")
        if len(devices) > 1 and "wlan0" not in devices:
            raise ValueError("Mais de uma interface Wi-Fi: defina a interface localmente antes de operar.")
        device = "wlan0" if "wlan0" in devices else devices[0]
        self.last_interface = device
        return device

    def _check_robot_route(self, interface):
        host = str(ipaddress.ip_address(self.robot_host()))
        try:
            result = subprocess.run(["ip", "-j", "route", "get", host], capture_output=True, text=True, timeout=3)
            routes = json.loads(result.stdout) if result.returncode == 0 else []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise ValueError("Não foi possível verificar a rota do robô; operação Wi-Fi bloqueada.")
        if not routes or routes[0].get("dev") == interface:
            raise ValueError("Robô usa Wi-Fi ou rota não verificada. Troca bloqueada para preservar sua comunicação.")

    def status(self, fresh=False):
        with self.lock:
            if not fresh and time.monotonic() - self.cache_time < 5:
                return dict(self.cached)
            try:
                interface = self.interface()
                data = self._run(["-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", interface])
                values = {}
                for row in data.splitlines():
                    key, _, value = row.partition(":")
                    values[key] = value.replace("\\:", ":").replace("\\\\", "\\")
                state = values.get("GENERAL.STATE", "")
                connection = values.get("GENERAL.CONNECTION", "--")
                ssid = None
                if state.startswith("100"):
                    active_uuid = self._run(["-g", "GENERAL.CON-UUID", "device", "show", interface])
                    if active_uuid and active_uuid != "--":
                        ssid = self._run(["-g", "802-11-wireless.ssid", "connection", "show", "uuid", active_uuid])
                self.cached = {"status": "CONNECTED" if state.startswith("100") else "DISCONNECTED",
                               "interface": interface, "ssid": ssid, "connection": connection,
                               "ip": values.get("IP4.ADDRESS[1]", "--"),
                               "profile_uuid": active_uuid if state.startswith("100") else None}
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.cached = {"status": "UNAVAILABLE", "interface": self.last_interface,
                               "message": str(error) if isinstance(error, ValueError) else "Falha ao consultar NetworkManager."}
            self.cache_time = time.monotonic()
            return dict(self.cached)

    def scan(self):
        interface = self.interface()
        self._run(["device", "wifi", "rescan", "ifname", interface])
        # Keep text lines in public JSON to avoid misinterpreting escaped SSIDs.
        rows = self._run(["-t", "--escape", "yes", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", interface])
        return {"interface": interface, "networks": rows.splitlines()[:60]}

    def change(self, action, data):
        with self.lock:
            interface = self.interface()
            self._check_robot_route(interface)
            if action == "disconnect":
                self._run(["device", "disconnect", interface])
            elif action == "reconnect":
                # Use only application-created profiles bound to the Wi-Fi interface.
                profile = data.get("profile_uuid")
                try:
                    profile = str(uuid.UUID(profile))
                except (ValueError, TypeError, AttributeError):
                    raise ValueError("Informe UUID de um perfil Futurecom criado neste painel.")
                kind = self._run(["-g", "connection.type", "connection", "show", "uuid", profile])
                bound = self._run(["-g", "connection.interface-name", "connection", "show", "uuid", profile])
                name = self._run(["-g", "connection.id", "connection", "show", "uuid", profile])
                if kind != "802-11-wireless" or bound != interface or not name.startswith("futurecom-wifi-"):
                    raise ValueError("Perfil não pertence à conexão Wi-Fi gerenciada pelo painel.")
                self._run(["connection", "up", "uuid", profile, "ifname", interface])
            elif action == "connect":
                ssid, password = data.get("ssid", ""), data.get("password", "")
                if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(c in ssid for c in "\r\n\x00"):
                    raise ValueError("SSID inválido.")
                if not isinstance(password, str) or any(c in password for c in "\r\n\x00"):
                    raise ValueError("Senha inválida.")
                if password and not (8 <= len(password) <= 63 or (len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password))):
                    raise ValueError("Senha WPA deve ter 8–63 caracteres ou 64 dígitos hexadecimais.")
                profile = str(uuid.uuid4())
                name = "futurecom-wifi-" + profile[:8]
                self._run(["connection", "add", "type", "wifi", "ifname", interface, "con-name", name,
                           "ssid", ssid, "connection.uuid", profile, "connection.autoconnect", "no",
                           "ipv4.method", "auto", "ipv4.never-default", "yes", "ipv6.method", "auto", "ipv6.never-default", "yes"])
                try:
                    if password:
                        self._run(["connection", "modify", "uuid", profile, "wifi-sec.key-mgmt", "wpa-psk",
                                   "wifi-sec.psk-flags", "0"])
                        with tempfile.TemporaryDirectory(prefix="futurecom-wifi-") as private:
                            password_path = os.path.join(private, "credentials")
                            fd = os.open(password_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                            with os.fdopen(fd, "w") as stream:
                                stream.write("802-11-wireless-security.psk:" + password + "\n")
                            self._run(["connection", "up", "uuid", profile, "ifname", interface, "passwd-file", password_path])
                        # Do not store the secret in settings.json; NetworkManager owns the connection.
                    else:
                        self._run(["connection", "up", "uuid", profile, "ifname", interface])
                except Exception:
                    try:
                        self._run(["connection", "delete", "uuid", profile])
                    except Exception:
                        pass
                    raise
                self._run(["connection", "modify", "uuid", profile, "connection.autoconnect", "yes"])
            else:
                raise ValueError("Ação Wi-Fi inválida.")
            self.cache_time = 0
            return {"ok": True, "profile_uuid": profile if action in ("connect", "reconnect") else None}
