"""
LADA - CV Detector (Computer Vision Fallback)
OpenCV template matching — only used when accessibility fails.
Never used for blind pixel clicking.
"""

import asyncio
import os
import subprocess
from typing import Optional, Tuple, List
from pathlib import Path
from utils.logger import LADALogger

logger = LADALogger("CV_DETECTOR")

TEMPLATE_DIR = Path(__file__).parent.parent / "memory" / "templates"


class CVDetector:
    """
    OpenCV-based template matching.
    Fallback only — used when AT-SPI tree fails.
    """

    def __init__(self):
        self.cv = None
        self.np = None
        self._available = False
        self.confidence_threshold = 0.80
        self.failure_counts: dict = {}     # template → failure count
        self.MAX_TEMPLATE_FAILURES = 3     # update template after this many fails

    async def initialize(self) -> bool:
        """Initialize OpenCV."""
        try:
            import cv2
            import numpy as np
            self.cv = cv2
            self.np = np
            TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
            self._available = True
            logger.info("OpenCV initialized.")
            return True
        except ImportError:
            logger.warning("OpenCV (cv2) not installed. CV layer disabled.")
            self._available = False
            return False

    def is_available(self) -> bool:
        return self._available

    # ── SCREENSHOT ────────────────────────────────────────────

    def take_screenshot(self, path: str = "/tmp/lada_screen.png") -> bool:
        """Take a screenshot using scrot or import."""
        for cmd in [["scrot", path], ["import", "-window", "root", path]]:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and os.path.exists(path):
                return True
        logger.warning("Could not take screenshot via scrot or import.")
        return False

    def load_screenshot(
        self,
        path: str = "/tmp/lada_screen.png"
    ):
        """Load screenshot as OpenCV image."""
        if not self._available:
            return None
        try:
            img = self.cv.imread(path)
            if img is None:
                logger.warning(f"Could not load image: {path}")
            return img
        except Exception as e:
            logger.warning(f"load_screenshot error: {e}")
            return None

    # ── TEMPLATE MATCHING ─────────────────────────────────────

    def find_template(
        self,
        template_name: str,
        screenshot=None,
        confidence: Optional[float] = None
    ) -> Optional[Tuple[int, int]]:
        """
        Find a template in the screenshot.
        Returns (x, y) center coordinates if found, None otherwise.
        """
        if not self._available:
            return None

        confidence = confidence or self.confidence_threshold

        # Load template
        template_path = TEMPLATE_DIR / f"{template_name}.png"
        if not template_path.exists():
            logger.warning(f"Template not found: {template_path}")
            return None

        template = self.cv.imread(str(template_path))
        if template is None:
            logger.warning(f"Could not load template: {template_path}")
            return None

        # Load screenshot if not provided
        if screenshot is None:
            self.take_screenshot()
            screenshot = self.load_screenshot()

        if screenshot is None:
            return None

        # Match template
        result = self.cv.matchTemplate(
            screenshot, template,
            self.cv.TM_CCOEFF_NORMED
        )
        min_val, max_val, min_loc, max_loc = self.cv.minMaxLoc(result)

        if max_val >= confidence:
            # Calculate center of matched region
            th, tw = template.shape[:2]
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2
            logger.debug(f"Template '{template_name}' found at ({cx}, {cy}) confidence={max_val:.3f}")

            # Reset failure count on success
            self.failure_counts[template_name] = 0
            return (cx, cy)
        else:
            logger.debug(
                f"Template '{template_name}' not found. "
                f"Best match: {max_val:.3f} < {confidence}"
            )
            # Track failure
            self.failure_counts[template_name] = (
                self.failure_counts.get(template_name, 0) + 1
            )
            # Adaptive: flag for template update if failing too often
            if self.failure_counts[template_name] >= self.MAX_TEMPLATE_FAILURES:
                logger.warning(
                    f"Template '{template_name}' failed {self.MAX_TEMPLATE_FAILURES} times. "
                    f"Consider updating template."
                )
                self.failure_counts[template_name] = 0

            return None

    def find_all_templates(
        self,
        template_name: str,
        screenshot=None,
        confidence: Optional[float] = None
    ) -> List[Tuple[int, int]]:
        """Find all occurrences of a template on screen."""
        if not self._available:
            return []

        confidence = confidence or self.confidence_threshold
        template_path = TEMPLATE_DIR / f"{template_name}.png"

        if not template_path.exists():
            return []

        template = self.cv.imread(str(template_path))
        if template is None:
            return []

        if screenshot is None:
            self.take_screenshot()
            screenshot = self.load_screenshot()

        if screenshot is None:
            return []

        result = self.cv.matchTemplate(
            screenshot, template,
            self.cv.TM_CCOEFF_NORMED
        )
        locations = self.np.where(result >= confidence)

        th, tw = template.shape[:2]
        found = []
        for y, x in zip(*locations):
            cx = int(x) + tw // 2
            cy = int(y) + th // 2
            found.append((cx, cy))

        return found

    # ── TEXT DETECTION (OCR — optional) ───────────────────────

    def find_text_on_screen(self, text: str) -> Optional[Tuple[int, int]]:
        """
        Find text on screen using OCR (pytesseract).
        Used only as last resort — AT-SPI is preferred.
        """
        try:
            import pytesseract
            from PIL import Image

            self.take_screenshot()
            screenshot_path = "/tmp/lada_screen.png"

            if not os.path.exists(screenshot_path):
                return None

            img = Image.open(screenshot_path)
            data = pytesseract.image_to_data(
                img,
                output_type=pytesseract.Output.DICT
            )

            text_lower = text.lower()
            for i, word in enumerate(data["text"]):
                if text_lower in word.lower() and data["conf"][i] > 50:
                    x = data["left"][i] + data["width"][i] // 2
                    y = data["top"][i] + data["height"][i] // 2
                    logger.debug(f"OCR found '{text}' at ({x}, {y})")
                    return (x, y)

        except ImportError:
            logger.debug("pytesseract not available for OCR.")
        except Exception as e:
            logger.warning(f"OCR error: {e}")

        return None

    # ── TEMPLATE MANAGEMENT ───────────────────────────────────

    def save_template(
        self,
        name: str,
        region: Optional[Tuple[int, int, int, int]] = None,
        screenshot=None
    ) -> bool:
        """
        Save a region as a template for future matching.
        region: (x, y, width, height)
        """
        if not self._available:
            return False

        if screenshot is None:
            self.take_screenshot()
            screenshot = self.load_screenshot()

        if screenshot is None:
            return False

        try:
            if region:
                x, y, w, h = region
                cropped = screenshot[y:y+h, x:x+w]
            else:
                cropped = screenshot

            template_path = TEMPLATE_DIR / f"{name}.png"
            success = self.cv.imwrite(str(template_path), cropped)
            if success:
                logger.info(f"Template saved: {template_path}")
            return success

        except Exception as e:
            logger.warning(f"save_template error: {e}")
            return False

    def list_templates(self) -> List[str]:
        """List all available templates."""
        if not TEMPLATE_DIR.exists():
            return []
        return [f.stem for f in TEMPLATE_DIR.glob("*.png")]

    def delete_template(self, name: str) -> bool:
        """Delete a template file."""
        template_path = TEMPLATE_DIR / f"{name}.png"
        if template_path.exists():
            template_path.unlink()
            logger.info(f"Template deleted: {name}")
            return True
        return False

    # ── SCREEN CHANGE DETECTION ───────────────────────────────

    def detect_screen_change(
        self,
        before,
        after,
        threshold: float = 0.05
    ) -> bool:
        """
        Check if significant screen change occurred.
        Returns True if change detected.
        """
        if not self._available or before is None or after is None:
            return False

        try:
            # Convert to grayscale
            gray_before = self.cv.cvtColor(before, self.cv.COLOR_BGR2GRAY)
            gray_after = self.cv.cvtColor(after, self.cv.COLOR_BGR2GRAY)

            # Compute difference
            diff = self.cv.absdiff(gray_before, gray_after)
            _, thresh = self.cv.threshold(diff, 30, 255, self.cv.THRESH_BINARY)

            # Calculate change ratio
            total_pixels = thresh.shape[0] * thresh.shape[1]
            changed_pixels = self.np.count_nonzero(thresh)
            change_ratio = changed_pixels / total_pixels

            return change_ratio > threshold

        except Exception as e:
            logger.warning(f"detect_screen_change error: {e}")
            return False
