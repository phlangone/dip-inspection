"""Standalone camera and OCR development interface (not the production dashboard)."""
import cv2
import re
import threading
import time

from flask import Flask, Response, jsonify, render_template_string
from rapidocr import RapidOCR


# ============================================================
# Configuration
# ============================================================

CAMERA_DEVICE = "/dev/video0"

FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_FPS = 30

MIN_PN_SCORE = 70
MIN_MARGIN = 10

FAMILY_PATTERNS = [
    r"^(SN)?74(?:LS|ALS|F|HC|HCT|AC|ACT|AHC|AHCT|LVC|LV|S|AS)?[A-Z0-9]+$",
    r"^(CD|HEF|HCF|MC)?4[0-9]{3,}[A-Z0-9]*$",
    r"^(LM|NE|TL|UA|ULN|ULQ|MAX|AD|OP|CA|TDA|MCP|PIC|ATMEGA|ATTINY|STM32)[A-Z0-9\-]+$",
]


# ============================================================
# Initialization
# ============================================================

app = Flask(__name__)

camera = cv2.VideoCapture(
    CAMERA_DEVICE,
    cv2.CAP_V4L2
)

camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
camera.set(cv2.CAP_PROP_FPS, FRAME_FPS)

if not camera.isOpened():
    raise RuntimeError(
        f"Não foi possível abrir a câmera em {CAMERA_DEVICE}."
    )

ocr = RapidOCR()

frame_lock = threading.Lock()
ocr_lock = threading.Lock()

latest_frame = None


# ============================================================
# Generic part-number identification
# ============================================================

def normalize_text(text):
    text = text.upper().strip()

    return re.sub(
        r"[^A-Z0-9\-]",
        "",
        text
    )


def count_transitions(text):
    transitions = 0

    for a, b in zip(text, text[1:]):
        if a.isalpha() != b.isalpha():
            transitions += 1

    return transitions


def matches_family_pattern(text):
    for pattern in FAMILY_PATTERNS:
        if re.match(pattern, text):
            return True

    return False


def pn_score(text, ocr_confidence):
    """Return a heuristic part-number plausibility score from 0 to 100, not a statistical probability."""

    if not text:
        return 0

    score = 0
    length = len(text)

    has_letter = any(c.isalpha() for c in text)
    has_digit = any(c.isdigit() for c in text)

    letters = sum(c.isalpha() for c in text)
    digits = sum(c.isdigit() for c in text)

    # RapidOCR confidence
    score += ocr_confidence * 35

    # Part numbers typically combine letters and digits.
    if has_letter and has_digit:
        score += 20
    else:
        score -= 25

    # Plausible length
    if 5 <= length <= 14:
        score += 15
    elif 4 <= length <= 18:
        score += 7
    else:
        score -= 10

    # Letter/digit transitions
    score += min(
        count_transitions(text) * 3,
        10
    )

    # Known semiconductor families
    if matches_family_pattern(text):
        score += 25

    # Many part numbers start with a letter.
    if text[0].isalpha():
        score += 5

    # Alphabetic suffixes are common.
    if (
        length >= 2
        and text[-1].isalpha()
        and any(c.isdigit() for c in text[:-1])
    ):
        score += 4

    # Penalties
    if digits / length > 0.80:
        score -= 15

    if letters / length > 0.90:
        score -= 15

    if (
        re.match(r"^[0-9]{2,4}[A-Z]", text)
        and not matches_family_pattern(text)
    ):
        score -= 12

    return max(
        0,
        min(100, score)
    )


def analyze_results(result):
    candidates = []

    if result is None or result.txts is None:
        return candidates

    for text, confidence in zip(
        result.txts,
        result.scores
    ):
        normalized = normalize_text(text)

        if not normalized:
            continue

        candidates.append({
            "original": str(text),
            "text": normalized,
            "ocr_confidence": float(confidence),
            "pn_score": float(
                pn_score(
                    normalized,
                    float(confidence)
                )
            ),
        })

    candidates.sort(
        key=lambda item: item["pn_score"],
        reverse=True
    )

    return candidates


def select_part_number(candidates):
    if not candidates:
        return None, "SEM_TEXTO"

    best = candidates[0]

    if best["pn_score"] < MIN_PN_SCORE:
        return best, "BAIXA_CONFIANCA"

    if len(candidates) >= 2:
        second = candidates[1]

        margin = (
            best["pn_score"]
            - second["pn_score"]
        )

        if margin < MIN_MARGIN:
            return best, "AMBIGUO"

    return best, "IDENTIFICADO"


# ============================================================
# Camera
# ============================================================

def camera_loop():
    global latest_frame

    while True:
        ret, frame = camera.read()

        if not ret:
            time.sleep(0.05)
            continue

        with frame_lock:
            latest_frame = frame.copy()


def get_frame():
    with frame_lock:
        if latest_frame is None:
            return None

        return latest_frame.copy()


def generate_video():
    while True:
        frame = get_frame()

        if frame is None:
            time.sleep(0.05)
            continue

        ret, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, 80]
        )

        if not ret:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + buffer.tobytes()
            + b"\r\n"
        )

        # Approximately 20 fps is sufficient for the UI.
        time.sleep(0.05)


# ============================================================
# Web interface
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>Futurecom 2026 - Inspeção de CI</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            background: #101318;
            color: #f3f5f7;
            font-family: Arial, Helvetica, sans-serif;
        }

        .page {
            max-width: 1180px;
            margin: 0 auto;
            padding: 28px;
        }

        h1 {
            margin: 0;
            font-size: 30px;
        }

        .subtitle {
            margin-top: 6px;
            margin-bottom: 24px;
            color: #9da5af;
        }

        .grid {
            display: grid;
            grid-template-columns: 1.55fr 1fr;
            gap: 20px;
        }

        .card {
            background: #1a1f26;
            border: 1px solid #2b323b;
            border-radius: 14px;
            overflow: hidden;
        }

        .card-title {
            padding: 14px 16px;
            font-weight: bold;
            border-bottom: 1px solid #2b323b;
        }

        .camera {
            background: #000;
        }

        .camera img {
            width: 100%;
            display: block;
        }

        .controls {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            padding: 16px;
        }

        button {
            border: 0;
            border-radius: 9px;
            padding: 12px 18px;
            font-size: 15px;
            cursor: pointer;
        }

        button:disabled {
            opacity: 0.55;
            cursor: wait;
        }

        .panel {
            padding: 18px;
        }

        .pn {
            font-size: 34px;
            font-weight: bold;
            word-break: break-all;
        }

        .status {
            display: inline-block;
            margin-top: 10px;
            padding: 7px 10px;
            border-radius: 8px;
            background: #2a3039;
            font-weight: bold;
        }

        .metric {
            margin-top: 12px;
            color: #c6cbd2;
        }

        .section-title {
            margin-top: 22px;
            margin-bottom: 8px;
            font-weight: bold;
        }

        .candidate {
            margin-top: 10px;
            padding: 12px;
            background: #13171d;
            border-radius: 9px;
        }

        .candidate-text {
            font-size: 18px;
            font-weight: bold;
        }

        .candidate-info {
            margin-top: 4px;
            color: #9da5af;
            font-size: 14px;
        }

        .placeholder {
            color: #858d97;
        }

        .inspection-box {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-top: 10px;
        }

        .inspection-item {
            padding: 12px;
            border-radius: 9px;
            background: #13171d;
        }

        .inspection-label {
            color: #9da5af;
            font-size: 13px;
        }

        .inspection-value {
            margin-top: 5px;
            font-size: 18px;
            font-weight: bold;
        }

        @media (max-width: 850px) {
            .grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>

<body>

<div class="page">

    <h1>Futurecom 2026</h1>

    <div class="subtitle">
        Inspeção inteligente de CI DIP — UNO Q
    </div>

    <div class="grid">

        <div class="card">

            <div class="card-title">
                Câmera
            </div>

            <div class="camera">
                <img src="/video_feed">
            </div>

            <div class="controls">

                <button onclick="captureImage()">
                    Capturar imagem
                </button>

                <button id="ocrButton" onclick="runOCR()">
                    Executar OCR
                </button>

            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Resultado
            </div>

            <div class="panel">

                <div id="ocrResult">
                    <div class="placeholder">
                        Aguardando identificação...
                    </div>
                </div>

                <div class="section-title">
                    Inspeção de terminais
                </div>

                <div class="inspection-box">

                    <div class="inspection-item">
                        <div class="inspection-label">
                            Lateral A
                        </div>
                        <div class="inspection-value placeholder">
                            Pendente
                        </div>
                    </div>

                    <div class="inspection-item">
                        <div class="inspection-label">
                            Lateral B
                        </div>
                        <div class="inspection-value placeholder">
                            Pendente
                        </div>
                    </div>

                </div>

            </div>

        </div>

    </div>

</div>


<script>

async function captureImage() {

    const response = await fetch("/capture");
    const data = await response.json();

    if (!response.ok) {
        alert(data.error || "Erro ao capturar imagem.");
        return;
    }

    alert("Imagem salva em " + data.file);
}


async function runOCR() {

    const button = document.getElementById("ocrButton");
    const panel = document.getElementById("ocrResult");

    button.disabled = true;

    panel.innerHTML =
        '<div class="placeholder">Executando RapidOCR...</div>';

    try {

        const response = await fetch("/ocr");
        const data = await response.json();

        if (!response.ok) {

            panel.innerHTML =
                '<div class="placeholder">' +
                (data.error || "Erro ao executar OCR.") +
                '</div>';

            return;
        }

        let html = "";

        if (data.best) {

            html +=
                '<div class="pn">' +
                data.best.text +
                '</div>';

            html +=
                '<div class="status">' +
                data.status +
                '</div>';

            html +=
                '<div class="metric">' +
                'Confiança OCR: ' +
                (data.best.ocr_confidence * 100).toFixed(2) +
                '%' +
                '</div>';

            html +=
                '<div class="metric">' +
                'Score de PN: ' +
                data.best.pn_score.toFixed(1) +
                '/100' +
                '</div>';

        } else {

            html +=
                '<div class="pn">INDETERMINADO</div>';

            html +=
                '<div class="status">' +
                data.status +
                '</div>';
        }

        if (data.raw_texts.length > 0) {

            html +=
                '<div class="section-title">' +
                'Textos detectados' +
                '</div>';

            data.raw_texts.forEach(item => {

                html +=
                    '<div class="candidate">' +

                    '<div class="candidate-text">' +
                    item.text +
                    '</div>' +

                    '<div class="candidate-info">' +
                    'OCR: ' +
                    (item.score * 100).toFixed(2) +
                    '%' +
                    '</div>' +

                    '</div>';
            });
        }

        panel.innerHTML = html;

    } catch (error) {

        panel.innerHTML =
            '<div class="placeholder">Erro: ' +
            error +
            '</div>';

    } finally {

        button.disabled = false;
    }
}

</script>

</body>
</html>
"""


# ============================================================
# Flask routes
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_video(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/capture")
def capture():
    frame = get_frame()

    if frame is None:
        return jsonify({
            "error": "Nenhum frame disponível."
        }), 500

    filename = "capture_ui.jpg"

    ok = cv2.imwrite(
        filename,
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, 100]
    )

    if not ok:
        return jsonify({
            "error": "Falha ao salvar a imagem."
        }), 500

    return jsonify({
        "file": filename
    })


@app.route("/ocr")
def run_ocr():
    frame = get_frame()

    if frame is None:
        return jsonify({
            "error": "Nenhum frame disponível."
        }), 500

    # Run RapidOCR directly on the in-memory frame.
    # No fixed ROI, Tesseract, or intermediate file.
    with ocr_lock:
        result = ocr(frame)

    raw_texts = []

    if result is not None and result.txts is not None:

        for text, score in zip(
            result.txts,
            result.scores
        ):
            raw_texts.append({
                "text": str(text),
                "score": float(score),
            })

    candidates = analyze_results(result)

    best, status = select_part_number(
        candidates
    )

    # Development diagnostic log
    print()
    print("========== OCR ==========")

    if raw_texts:
        for item in raw_texts:
            print(
                f"{item['text']} | "
                f"{item['score'] * 100:.2f}%"
            )
    else:
        print("Nenhum texto detectado.")

    if best:
        print(
            f"PN: {best['text']} | "
            f"Status: {status}"
        )

    print("=========================")
    print()

    return jsonify({
        "status": status,
        "best": best,
        "raw_texts": raw_texts,
        "candidates": candidates,
    })


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    camera_thread = threading.Thread(
        target=camera_loop,
        daemon=True
    )

    camera_thread.start()

    print()
    print("Futurecom 2026 - Camera UI")
    print("--------------------------")
    print(f"Câmera: {CAMERA_DEVICE}")
    print("Porta: 5000")
    print()
    print("Abra no navegador:")
    print("http://IP_DO_UNOQ:5000")
    print()

    try:

        app.run(
            host="0.0.0.0",
            port=5000,
            debug=False,
            threaded=True
        )

    finally:

        camera.release()
