# Deployment and diagnostics

## Environment

On a compatible Debian-based UNO Q installation, install system dependencies as needed:

```sh
sudo apt install python3-venv python3-pip git v4l-utils libgl1 libglib2.0-0 iproute2
```

Use the virtual environment and requirements described in the README. Do not replace the official RTDE Python client with `ur_rtde`: this application imports `rtde.rtde`. The requirements are not a lockfile. Before changing a functioning station, export its package versions with `python -m pip freeze` and record the OS and model versions. Check model executable dependencies with `ldd` if necessary.

Prepare RapidOCR's local model cache while internet access is available. Worker initialization blocks socket connections during OCR loading; missing cached artifacts must be resolved before offline use. Test a cold start without internet while preserving local robot connectivity.

## Administrator access

Generate a password hash interactively from the activated virtual environment:

```sh
python - <<'PY'
from getpass import getpass
from werkzeug.security import generate_password_hash
print(generate_password_hash(getpass('Administrator password: ')))
PY
```

Place the resulting hash in `deployment/futurecom.env`, inside single quotes. Keep this file private. The example file is not a working credential. The dashboard is intended for a trusted local network; it does not configure HTTPS. Do not expose it directly to the internet.

## Optional systemd service

Adjust `deployment/futurecom.service` to match the real user, group, project path, virtual environment and environment-file path. The supplied example uses `/home/arduino/futurecom`; update it if cloning as `dip-inspection`. Install and start only after completing the initial hardware checks:

```sh
sudo cp deployment/futurecom.service /etc/systemd/system/futurecom.service
sudo systemctl daemon-reload
sudo systemctl enable --now futurecom
journalctl -u futurecom -n 100 --no-pager
```

Do not run a second supervisor or camera utility against the same device. Optional MQTT may use a local Mosquitto broker.

## Wi-Fi management (optional)

The dashboard uses NetworkManager (`nmcli`) and checks the route to the robot before Wi-Fi changes. It must not alter the robot Ethernet connection. If the service user already has the necessary permissions, the restricted privileged helper is unnecessary. Keep `FUTURECOM_WIFI_HELPER=0` unless a local administrator installs and validates it.

The optional helper must be installed as root-owned, non-user-writable `/usr/local/libexec/futurecom-wifi`, with a root-owned `/etc/futurecom-wifi-interface` specifying the actual Wi-Fi interface. Review and install the supplied `futurecom-wifi.sudoers` rule via `visudo`, matching the service user. Validate it with `sudo visudo -c` before enabling the helper. Never grant general passwordless sudo or unrestricted nmcli access.

## Standalone tools

Stop the station application and secure the robot before accessing the camera independently.

| Command | Purpose |
| --- | --- |
| `python -m tools.test_presence --capture --reference empty_reference_new.jpg` | Capture a new empty-rail reference without replacing the existing one |
| `python -m tools.test_presence --reference empty_reference_new.jpg --samples 0` | Monitor presence scores continuously; Ctrl+C to stop |
| `python -m tools.dataset_collector_simple` | Browser-based image collector on port 5050 |
| `python -m tools.camera_ui` | Legacy standalone OCR and camera diagnostic on port 5000 |

The presence diagnostic reads `settings.json`; review the reference filename before starting. Test empty and occupied rail conditions, then replace the production reference only after reviewing the results. The standalone browser tools are development utilities, not authenticated production interfaces.
