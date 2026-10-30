import io
import subprocess
import wave

import numpy as np
import onnx_asr

from app.application.errors import AssistantError, ErrorCode
from app.application.ports.outbound import SpeechToText

RATE = 16000
INT16_SCALE = 32768
FFMPEG_TIMEOUT_SECONDS = 15


class GigaAMSpeechToText(SpeechToText):
    def __init__(self, model, max_seconds: int = 30):
        self._model = model
        self._max = max_seconds

    @classmethod
    def load(cls, name: str) -> "GigaAMSpeechToText":
        return cls(onnx_asr.load_model(name))

    def decode(self, audio: bytes) -> np.ndarray:
        if audio[:4] == b"RIFF":  # WAV из голосового цикла уже 16 кГц моно: ffmpeg не нужен (~110 мс)
            try:
                with wave.open(io.BytesIO(audio)) as reader:
                    if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) == (RATE, 1, 2):
                        frames = min(reader.getnframes(), (self._max + 1) * RATE)
                        return np.frombuffer(reader.readframes(frames), np.int16).astype(np.float32) / INT16_SCALE
            except (wave.Error, EOFError):
                pass
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-protocol_whitelist",
                    "pipe",
                    "-i",
                    "pipe:0",
                    "-t",
                    str(self._max + 1),
                    "-f",
                    "f32le",
                    "-ac",
                    "1",
                    "-ar",
                    str(RATE),
                    "pipe:1",
                ],
                input=audio,
                capture_output=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            raise AssistantError(ErrorCode.STT_FAILED, str(e)) from e
        if proc.returncode != 0:
            raise AssistantError(ErrorCode.STT_FAILED, proc.stderr.decode(errors="replace"))
        return np.frombuffer(proc.stdout, np.float32)

    def transcribe(self, audio: bytes) -> str:
        samples = self.decode(audio)
        if len(samples) > self._max * RATE:
            raise AssistantError(ErrorCode.AUDIO_TOO_LONG)
        if len(samples) == 0:
            return ""
        try:
            return self._model.recognize(samples, sample_rate=RATE)
        except Exception as e:
            raise AssistantError(ErrorCode.STT_FAILED, str(e)) from e
