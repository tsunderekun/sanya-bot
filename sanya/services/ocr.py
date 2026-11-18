import asyncio
import logging
import subprocess

from ..config import OCR_ENABLED, TESSERACT_CMD

_IMAGE_LIMIT = 10_000_000
logger = logging.getLogger(__name__)


def _recognize(image: bytes) -> str:
    result = subprocess.run(
        [TESSERACT_CMD, "stdin", "stdout", "-l", "rus+eng", "--psm", "11"],
        input=image,
        capture_output=True,
        timeout=6,
        check=True,
    )
    text = result.stdout[:6000].decode("utf-8", errors="replace")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())[:820]


async def recognize_image_text(image: bytes) -> str:
    if not OCR_ENABLED or not image or len(image) > _IMAGE_LIMIT:
        return ""
    try:
        return await asyncio.to_thread(_recognize, image)
    except (OSError, subprocess.SubprocessError) as error:
        logger.debug("OCR недоступен: %s", type(error).__name__)
        return ""
