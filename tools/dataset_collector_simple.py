"""Standalone browser-based image collector for the two inspection sides."""
from flask import Flask, Response, request, redirect, url_for, render_template_string
from pathlib import Path
import threading
import time
import cv2

from dip_inspection.vision.camera_manager import CameraManager

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = BASE_DIR / "dataset"

DIRS = {
    "side_a_normal": DATASET_DIR / "side_a" / "normal",
    "side_a_anormal": DATASET_DIR / "side_a" / "anormal",
    "side_b_normal": DATASET_DIR / "side_b" / "normal",
    "side_b_anormal": DATASET_DIR / "side_b" / "anormal",
}

for path in DIRS.values():
    path.mkdir(parents=True, exist_ok=True)


class CameraStream:
    """Capture frames continuously for preview and manual dataset collection."""
    def __init__(self):
        self.cam = CameraManager()
        self.lock = threading.Lock()
        self.frame = None
        self.running = False
        self.thread = None

    def start(self):
        self.cam.connect()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while self.running:
            try:
                frame = self.cam.capture()
                with self.lock:
                    self.frame = frame.copy()
            except Exception as e:
                print(f"[ERRO CAMERA] {e}")
                time.sleep(0.5)

            time.sleep(0.03)

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            return self.frame.copy()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.cam.close()


stream = CameraStream()


def count_images(path: Path) -> int:
    return len(list(path.glob("*.jpg")))


def next_filename(path: Path, prefix: str) -> Path:
    idx = count_images(path)
    return path / f"{prefix}_{idx:04d}.jpg"


def save_current_frame(key: str):
    """Save the latest camera frame to the selected dataset category."""
    frame = stream.get_frame()
    if frame is None:
        raise RuntimeError("Nenhum frame disponível.")

    out_dir = DIRS[key]
    filename = next_filename(out_dir, key)
    ok = cv2.imwrite(str(filename), frame)

    if not ok:
        raise RuntimeError(f"Falha ao salvar {filename}")

    return filename


@app.route("/")
def index():
    counters = {k: count_images(v) for k, v in DIRS.items()}
    return render_template_string(
        """
<!doctype html>
<html lang="pt-br">
<head>
    <meta charset="utf-8">
    <title>Dataset Collector</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f5f5f5;
            margin: 0;
            padding: 24px;
        }
        h1 {
            margin-top: 0;
        }
        .container {
            display: grid;
            grid-template-columns: 1.3fr 1fr;
            gap: 24px;
            align-items: start;
        }
        .card {
            background: white;
            border-radius: 12px;
            padding: 16px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        img {
            width: 100%;
            border-radius: 8px;
            background: #ddd;
        }
        .buttons {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-top: 16px;
        }
        button {
            width: 100%;
            padding: 16px;
            font-size: 16px;
            font-weight: bold;
            border: none;
            border-radius: 10px;
            cursor: pointer;
        }
        .normal {
            background: #1f9d55;
            color: white;
        }
        .anormal {
            background: #d64545;
            color: white;
        }
        .counts {
            margin-top: 20px;
            line-height: 1.9;
            font-size: 16px;
        }
        .footer {
            margin-top: 16px;
            color: #555;
        }
    </style>
</head>
<body>
    <h1>Coletor de Dataset</h1>

    <div class="container">
        <div class="card">
            <h2>Câmera</h2>
            <img src="{{ url_for('video_feed') }}" alt="camera">
        </div>

        <div class="card">
            <h2>Captura</h2>

            <div class="buttons">
                <form method="post" action="/capture/side_a_normal">
                    <button class="normal" type="submit">Side A - Normal</button>
                </form>

                <form method="post" action="/capture/side_a_anormal">
                    <button class="anormal" type="submit">Side A - Anormal</button>
                </form>

                <form method="post" action="/capture/side_b_normal">
                    <button class="normal" type="submit">Side B - Normal</button>
                </form>

                <form method="post" action="/capture/side_b_anormal">
                    <button class="anormal" type="submit">Side B - Anormal</button>
                </form>
            </div>

            <div class="counts">
                <strong>Contagens:</strong><br>
                Side A / Normal: {{ counters["side_a_normal"] }}<br>
                Side A / Anormal: {{ counters["side_a_anormal"] }}<br>
                Side B / Normal: {{ counters["side_b_normal"] }}<br>
                Side B / Anormal: {{ counters["side_b_anormal"] }}<br>
            </div>

            <div class="footer">
                Diretório base: <code>{{ dataset_dir }}</code>
            </div>
        </div>
    </div>
</body>
</html>
        """,
        counters=counters,
        dataset_dir=str(DATASET_DIR),
    )


@app.route("/capture/<key>", methods=["POST"])
def capture(key):
    if key not in DIRS:
        return "Categoria inválida", 400

    try:
        filename = save_current_frame(key)
        print(f"[OK] Salvo: {filename}")
    except Exception as e:
        print(f"[ERRO] {e}")

    return redirect(url_for("index"))


def gen_frames():
    while True:
        frame = stream.get_frame()

        if frame is None:
            time.sleep(0.05)
            continue

        ok, buffer = cv2.imencode(".jpg", frame)
        if not ok:
            continue

        jpg = buffer.tobytes()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        )

        time.sleep(0.03)


@app.route("/video_feed")
def video_feed():
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    stream.start()
    try:
        app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
    finally:
        stream.stop()
