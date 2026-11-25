"""RapidOCR-based part-number extraction and heuristic candidate ranking."""
import re
import threading

from rapidocr import RapidOCR


class OCRProcessor:

    """Normalize recognized text and select a plausible semiconductor part number."""
    MIN_PN_SCORE = 70
    MIN_MARGIN = 10

    FAMILY_PATTERNS = [
        r"^(SN)?74(?:LS|ALS|F|HC|HCT|AC|ACT|AHC|AHCT|LVC|LV|S|AS)?[A-Z0-9]+$",
        r"^(CD|HEF|HCF|MC)?4[0-9]{3,}[A-Z0-9]*$",
        r"^(LM|NE|TL|UA|ULN|ULQ|MAX|AD|OP|CA|TDA|MCP|PIC|ATMEGA|ATTINY|STM32)[A-Z0-9\-]+$",
    ]

    def __init__(self):
        print("[OCR] Carregando RapidOCR...")

        self.ocr = RapidOCR()
        self.lock = threading.Lock()

        print("[OCR] Pronto.")

    def _normalize_text(self, text):
        text = text.upper().strip()

        return re.sub(
            r"[^A-Z0-9\-]",
            "",
            text
        )

    def _count_transitions(self, text):
        transitions = 0

        for a, b in zip(
            text,
            text[1:]
        ):
            if a.isalpha() != b.isalpha():
                transitions += 1

        return transitions

    def _matches_family_pattern(self, text):
        for pattern in self.FAMILY_PATTERNS:
            if re.match(pattern, text):
                return True

        return False

    def _pn_score(
        self,
        text,
        ocr_confidence
    ):
        """Compute a bounded heuristic plausibility score, not a probability."""
        if not text:
            return 0

        score = 0

        length = len(text)

        has_letter = any(
            c.isalpha()
            for c in text
        )

        has_digit = any(
            c.isdigit()
            for c in text
        )

        letters = sum(
            c.isalpha()
            for c in text
        )

        digits = sum(
            c.isdigit()
            for c in text
        )

        # RapidOCR confidence
        score += ocr_confidence * 35

        # Part numbers typically combine letters
        # and digits.
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
            self._count_transitions(text) * 3,
            10
        )

        # Known family
        if self._matches_family_pattern(text):
            score += 25

        # Many part numbers start with a letter.
        if text[0].isalpha():
            score += 5

        # Alphabetic suffix
        if (
            length >= 2
            and text[-1].isalpha()
            and any(
                c.isdigit()
                for c in text[:-1]
            )
        ):
            score += 4

        # Penalty: too many digits
        if digits / length > 0.80:
            score -= 15

        # Penalty: too many letters
        if letters / length > 0.90:
            score -= 15

        if (
            re.match(
                r"^[0-9]{2,4}[A-Z]",
                text
            )
            and not self._matches_family_pattern(
                text
            )
        ):
            score -= 12

        return max(
            0,
            min(100, score)
        )

    def _analyze_results(self, result):
        candidates = []

        if (
            result is None
            or result.txts is None
        ):
            return candidates

        for text, confidence in zip(
            result.txts,
            result.scores
        ):
            normalized = (
                self._normalize_text(text)
            )

            if not normalized:
                continue

            candidates.append(
                {
                    "original": str(text),
                    "text": normalized,
                    "ocr_confidence": float(
                        confidence
                    ),
                    "pn_score": float(
                        self._pn_score(
                            normalized,
                            float(confidence)
                        )
                    )
                }
            )

        candidates.sort(
            key=lambda item:
                item["pn_score"],
            reverse=True
        )

        return candidates

    def _select_part_number(
        self,
        candidates
    ):
        if not candidates:
            return None, "SEM_TEXTO"

        best = candidates[0]

        if (
            best["pn_score"]
            < self.MIN_PN_SCORE
        ):
            return (
                best,
                "BAIXA_CONFIANCA"
            )

        if len(candidates) >= 2:
            second = candidates[1]

            margin = (
                best["pn_score"]
                - second["pn_score"]
            )

            if margin < self.MIN_MARGIN:
                return (
                    best,
                    "AMBIGUO"
                )

        return (
            best,
            "IDENTIFICADO"
        )

    def inspect(self, frame):
        """Return OCR candidates, identification status, and the selected part number."""
        if frame is None:
            raise ValueError(
                "Frame inválido."
            )

        with self.lock:
            result = self.ocr(frame)

        raw_texts = []

        if (
            result is not None
            and result.txts is not None
        ):
            for text, score in zip(
                result.txts,
                result.scores
            ):
                raw_texts.append(
                    {
                        "text": str(text),
                        "score": float(score)
                    }
                )

        candidates = (
            self._analyze_results(
                result
            )
        )

        best, status = (
            self._select_part_number(
                candidates
            )
        )

        return {
            "status": status,
            "best": best,
            "raw_texts": raw_texts,
            "candidates": candidates,
            "identified":
                status == "IDENTIFICADO",
            "part_number":
                best["text"]
                if best is not None
                else None
        }