import io
import threading
import time
import wave

import numpy as np
import pytest

from app.adapters.outbound.vosk_tts import VoskTTS


class FakeSynth:
    def __init__(self):
        self.active = self.max_active = 0
        self.texts = []

    def synth_audio(self, text, speaker_id=0):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.texts.append((text, speaker_id))
        time.sleep(0.05)
        self.active -= 1
        return np.zeros(100, dtype=np.int16)


def test_calls_never_overlap_and_text_normalized():
    f = FakeSynth()
    tts = VoskTTS(f, 1)
    ts = [threading.Thread(target=tts.synthesize, args=("в 9:30",)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert f.max_active == 1 and f.texts[0] == ("в девять тридцать", 1)


def test_concurrent_calls_synthesize_own_text():
    f = FakeSynth()
    tts = VoskTTS(f, 1)
    ts = [threading.Thread(target=tts.synthesize, args=(w,)) for w in ("один", "два", "три", "четыре")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(t for t, _ in f.texts) == sorted(["один", "два", "три", "четыре"])


@pytest.mark.model
def test_synthesize_wav():
    with wave.open(io.BytesIO(VoskTTS.load("vosk-model-tts-ru-0.9-multi", 1).synthesize("Слушаю"))) as w:
        assert w.getnframes() > 0 and w.getsampwidth() == 2 and w.getnchannels() == 1
