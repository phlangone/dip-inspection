"""Classical image-difference detector for part presence on the feed rail."""
import os

import cv2
import numpy as np


class PresenceDetector:

    """Compare a current frame against a calibrated empty-rail reference."""
    def __init__(
        self,
        reference_path="empty_reference.jpg",
        roi=None,
        pixel_threshold=25,
        presence_ratio_threshold=0.08,
        blur_kernel=5
    ):
        """
        Initialize the detector with an optional ROI (x, y, width, height).
        
        pixel_threshold is the intensity threshold; presence_ratio_threshold is the
        minimum fraction of changed pixels (0.08 means 8%). blur_kernel specifies
        the Gaussian kernel size. A None ROI uses the entire image.
        """

        self.reference_path = reference_path
        self.roi = roi

        self.pixel_threshold = pixel_threshold
        self.presence_ratio_threshold = (
            presence_ratio_threshold
        )

        self.blur_kernel = blur_kernel

        self.reference = None

        if os.path.exists(reference_path):
            self.load_reference()

    def _crop(self, frame):
        """Crop the region of interest."""

        if self.roi is None:
            return frame

        x, y, width, height = self.roi

        return frame[
            y:y + height,
            x:x + width
        ]

    def _prepare(self, frame):
        """Convert the ROI to grayscale and optionally apply Gaussian blur."""

        roi = self._crop(frame)

        gray = cv2.cvtColor(
            roi,
            cv2.COLOR_BGR2GRAY
        )

        if self.blur_kernel > 1:
            gray = cv2.GaussianBlur(
                gray,
                (
                    self.blur_kernel,
                    self.blur_kernel
                ),
                0
            )

        return gray

    def set_reference(
        self,
        frame,
        save=True
    ):
        """Set the current frame as the empty-rail reference and optionally save it."""

        if frame is None:
            raise ValueError(
                "Frame de referência inválido."
            )

        self.reference = (
            self._prepare(frame)
        )

        if save:
            ok = cv2.imwrite(
                self.reference_path,
                frame
            )

            if not ok:
                raise RuntimeError(
                    "Não foi possível salvar "
                    "a referência."
                )

        print(
            "[PRESENCE] Referência "
            "do trilho vazio definida."
        )

    def load_reference(self):
        """Load the saved reference image from disk."""

        frame = cv2.imread(
            self.reference_path
        )

        if frame is None:
            raise RuntimeError(
                f"Não foi possível carregar "
                f"{self.reference_path}."
            )

        self.reference = (
            self._prepare(frame)
        )

        print(
            "[PRESENCE] Referência carregada."
        )

    def detect(self, frame):
        """Return the presence decision, percentage score, changed ratio, mean intensity difference, and percentage threshold."""

        if frame is None:
            raise ValueError(
                "Frame inválido."
            )

        if self.reference is None:
            raise RuntimeError(
                "Referência do trilho vazio "
                "não definida."
            )

        current = self._prepare(frame)

        if (
            current.shape
            != self.reference.shape
        ):
            raise RuntimeError(
                "Frame atual e referência "
                "possuem tamanhos diferentes."
            )

        # Absolute difference between the empty rail
        # and the current image.
        difference = cv2.absdiff(
            self.reference,
            current
        )

        mean_difference = float(
            np.mean(difference)
        )

        # Pixels with sufficient intensity difference
        _, mask = cv2.threshold(
            difference,
            self.pixel_threshold,
            255,
            cv2.THRESH_BINARY
        )

        # Remove small noise regions.
        kernel = np.ones(
            (3, 3),
            dtype=np.uint8
        )

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            kernel
        )

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            kernel
        )

        changed_pixels = (
            cv2.countNonZero(mask)
        )

        total_pixels = mask.size

        changed_ratio = (
            changed_pixels
            / total_pixels
        )

        present = (
            changed_ratio
            >= self.presence_ratio_threshold
        )

        return {
            "present": bool(present),

            # Express as a percentage for
            # readability in logs and the UI.
            "score": float(
                changed_ratio * 100
            ),

            "changed_ratio": float(
                changed_ratio
            ),

            "mean_difference":
                mean_difference,

            "threshold": float(
                self.presence_ratio_threshold
                * 100
            )
        }