import asyncio
import io
import json
import logging
import re
import subprocess
import wave
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, ReplyParameters

log = logging.getLogger(__name__)


def voice_text(text: str, *, limit: int = 240) -> str:
    """Leave links, code, markup and long replies in text form."""
    if re.search(r"[`<>*\[\]|_@]|\b(?:https?://|www\.)|\b\w+\.[a-zа-я]{2,}\b", text, re.I):
        return ""
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit and re.search(r"[а-яё]", normalized, re.I) else ""


_CONCLUSION = re.compile(
    r"^\s*(?:итог|вывод|вердикт|если коротко|коротко)(?:\s*[:.\-–—]\s*|\s*$)", re.I
)


def detail_voice_text(text: str) -> str:
    """Read an existing conclusion or a short breakdown in full."""
    if "```" in text:
        return ""
    plain = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    lines = plain.strip().splitlines()
    for index, line in enumerate(lines):
        if match := _CONCLUSION.match(line):
            conclusion = "\n".join((line[match.end():], *lines[index + 1:])).strip()
            return voice_text(conclusion, limit=360)
    return voice_text(plain, limit=360)


class PiperVoiceReplies:
    def __init__(self, model_path: Path):
        self._model_path = model_path
        self._voice = None
        self._job: asyncio.Task[bytes | None] | None = None

    def _load(self):
        import onnxruntime
        from piper import PiperVoice
        from piper.config import PhonemeType, PiperConfig

        with Path(f"{self._model_path}.json").open(encoding="utf-8") as file:
            config = PiperConfig.from_dict(json.load(file))
        if config.phoneme_type is not PhonemeType.ESPEAK or config.espeak_voice != "ru":
            raise ValueError("Expected a Russian eSpeak-based Piper model")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        return PiperVoice(
            config=config,
            session=onnxruntime.InferenceSession(
                str(self._model_path), sess_options=options, providers=["CPUExecutionProvider"]
            ),
        )

    def _synthesize(self, text: str) -> bytes | None:
        try:
            if self._voice is None:
                # Piper logs the input text at DEBUG; never enable that in the bot.
                logging.getLogger("piper").setLevel(logging.WARNING)
                self._voice = self._load()
            wav = io.BytesIO()
            with wave.open(wav, "wb") as output:
                self._voice.synthesize_wav(text, output)
            encoded = subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
                    "-ac", "1", "-c:a", "libopus", "-b:a", "32k", "-application", "voip",
                    "-f", "ogg", "pipe:1",
                ],
                input=wav.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=True, timeout=2,
            ).stdout
            return encoded if encoded.startswith(b"OggS") and b"OpusHead" in encoded[:128] else None
        except Exception as exc:
            log.warning("Voice synthesis failed: %s", type(exc).__name__)
            return None

    async def render(self, text: str) -> bytes | None:
        # Shield the job so timeout/cancellation doesn't release the worker while
        # native inference is still running. Further requests fall back to text.
        if self._job is not None and not self._job.done():
            return None
        self._job = asyncio.create_task(asyncio.to_thread(self._synthesize, text))
        try:
            return await asyncio.wait_for(asyncio.shield(self._job), timeout=3)
        except TimeoutError:
            log.warning("Voice synthesis timed out")
            return None


async def send_voice_reply(bot: Bot, chat_id: int, reply_to_id: int, audio: bytes) -> list[int]:
    try:
        sent = await bot.send_voice(
            chat_id,
            BufferedInputFile(audio, filename="sanya.ogg"),
            reply_parameters=ReplyParameters(message_id=reply_to_id, allow_sending_without_reply=True),
        )
    except TelegramAPIError as exc:
        log.warning("Voice delivery failed: %s", type(exc).__name__)
        return []
    return [sent.message_id]
