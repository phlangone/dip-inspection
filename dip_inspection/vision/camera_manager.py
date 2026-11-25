"""USB camera discovery, configuration, and frame acquisition."""
import cv2
import glob
import os
import time


class CameraManager:

    """Manage a single OpenCV VideoCapture device."""
    def __init__(
        self,
        device=None,
        camera_name="USB 2.0 Camera",
        width=640,
        height=480,
        fps=30
    ):
        self.device = device
        self.auto_device = device is None
        self.camera_name = camera_name

        self.width = width
        self.height = height
        self.fps = fps

        self.cap = None

    def _find_device(self):
        """Locate the USB camera by its configured name."""

        devices = sorted(
            glob.glob("/dev/video*")
        )

        for device in devices:

            name_path = (
                f"/sys/class/video4linux/"
                f"{os.path.basename(device)}/name"
            )

            try:
                with open(name_path, "r") as file:
                    name = file.read().strip()

            except OSError:
                continue

            print(
                f"[CAMERA] "
                f"{device}: {name}"
            )

            if self.camera_name in name:
                return device

        raise RuntimeError(
            f"Câmera '{self.camera_name}' "
            "não encontrada."
        )

    def connect(self):
        """Locate, open, and configure the camera."""

        if self.cap is not None:
            return

        if self.auto_device:
            self.device = self._find_device()

        print(
            f"[CAMERA] Abrindo "
            f"{self.device}..."
        )

        self.cap = cv2.VideoCapture(
            self.device,
            cv2.CAP_V4L2
        )

        if not self.cap.isOpened():
            self.cap.release()
            self.cap = None

            raise RuntimeError(
                f"Não foi possível abrir "
                f"{self.device}."
            )

        # The project camera operates
        # in YUYV mode at 640x480.
        self.cap.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(
                *"YUYV"
            )
        )

        self.cap.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            self.width
        )

        self.cap.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            self.height
        )

        self.cap.set(
            cv2.CAP_PROP_FPS,
            self.fps
        )

        self._print_config()

        self._stabilize()

        print("[CAMERA] Pronta.")

    def _print_config(self):
        """Report the settings actually accepted by the driver."""

        width = self.cap.get(
            cv2.CAP_PROP_FRAME_WIDTH
        )

        height = self.cap.get(
            cv2.CAP_PROP_FRAME_HEIGHT
        )

        fps = self.cap.get(
            cv2.CAP_PROP_FPS
        )

        fourcc_value = int(
            self.cap.get(
                cv2.CAP_PROP_FOURCC
            )
        )

        fourcc = "".join(
            chr(
                (fourcc_value >> (8 * i))
                & 0xFF
            )
            for i in range(4)
        )

        print(
            f"[CAMERA] "
            f"{width:.0f}x{height:.0f} "
            f"@ {fps:.1f} FPS "
            f"({fourcc})"
        )

    def _stabilize(
        self,
        frames=30,
        delay=0.05
    ):
        """Discard initial frames to allow the camera to stabilize."""

        print(
            "[CAMERA] "
            "Estabilizando..."
        )

        for _ in range(frames):

            ok, frame = self.cap.read()

            if not ok or frame is None:
                raise RuntimeError(
                    "Falha durante "
                    "estabilização da câmera."
                )

            time.sleep(delay)

    def capture(
        self,
        flush_frames=3
    ):
        """Capture a fresh frame after discarding buffered frames."""

        if self.cap is None:
            raise RuntimeError(
                "Câmera não conectada."
            )

        frame = None

        for _ in range(
            flush_frames + 1
        ):
            ok, frame = self.cap.read()

            if not ok or frame is None:
                raise RuntimeError(
                    "Falha ao capturar frame."
                )

        return frame.copy()

    def get_frame(self):
        """Read a frame without discarding additional frames; used for live preview."""

        if self.cap is None:
            raise RuntimeError(
                "Câmera não conectada."
            )

        ok, frame = self.cap.read()

        if not ok or frame is None:
            raise RuntimeError(
                "Falha ao capturar frame."
            )

        return frame

    def close(self):
        """Release the camera device."""

        if self.cap is not None:

            self.cap.release()

            self.cap = None

            print(
                "[CAMERA] Desconectada."
            )

    def is_connected(self):
        return (
            self.cap is not None
            and self.cap.isOpened()
        )