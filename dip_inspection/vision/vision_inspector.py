"""Edge Impulse anomaly inference for the two terminal inspection views."""
import threading

from edge_impulse_linux.image import ImageImpulseRunner


class VisionInspector:
    """Load separate side-A and side-B inference runners and apply score thresholds."""
    SIDE_A_THRESHOLD = 290.0
    SIDE_B_THRESHOLD = 265.0

    def __init__(
        self,
        side_a_model="models/side_a.eim",
        side_b_model="models/side_b.eim"
    ):
        self.side_a_runner = ImageImpulseRunner(side_a_model)
        self.side_b_runner = ImageImpulseRunner(side_b_model)

        # Prevent concurrent calls to the same runner.
        self.side_a_lock = threading.Lock()
        self.side_b_lock = threading.Lock()

        print("[VISION] Carregando SIDE A...")
        self.side_a_info = self.side_a_runner.init()

        print("[VISION] Carregando SIDE B...")
        self.side_b_info = self.side_b_runner.init()

        print(
            "[VISION] SIDE A:",
            self.side_a_info["project"]["name"]
        )

        print(
            "[VISION] SIDE B:",
            self.side_b_info["project"]["name"]
        )

        print("[VISION] Modelos prontos.")

    def _inspect(self, frame, runner, threshold, lock):
        """Run inference using the exported preprocessing and compare the mean anomaly score with the threshold."""
        if frame is None:
            raise ValueError("Frame inválido.")

        with lock:
            features, processed = (
                runner.get_features_from_image_auto_studio_settings(
                    frame
                )
            )

            result = runner.classify(features)

        ei_result = result["result"]

        score = float(
            ei_result["visual_anomaly_mean"]
        )

        is_anomaly = score >= threshold

        is_anomaly = score >= threshold

        return {
            "score": score,
            "threshold": threshold,
            "anomaly": is_anomaly,
            "status": "ANOMALIA" if is_anomaly else "NORMAL",
            "timing": result.get("timing", {})
        }

    def inspect_side_a(self, frame):
        return self._inspect(
            frame=frame,
            runner=self.side_a_runner,
            threshold=self.SIDE_A_THRESHOLD,
            lock=self.side_a_lock
        )

    def inspect_side_b(self, frame):
        return self._inspect(
            frame=frame,
            runner=self.side_b_runner,
            threshold=self.SIDE_B_THRESHOLD,
            lock=self.side_b_lock
        )

    def stop(self):
        print("[VISION] Encerrando modelos...")

        self.side_a_runner.stop()
        self.side_b_runner.stop()