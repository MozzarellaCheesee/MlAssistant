import subprocess

from app.adapters.outbound import audio_player


def test_play_wav_passes_data(monkeypatch):
    calls = []
    monkeypatch.setattr(audio_player.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    audio_player.play_wav(b"RIFF", timeout=5)
    assert calls[0][1]["input"] == b"RIFF" and calls[0][1]["timeout"] == 5


def test_play_wav_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("pw-play", 1)
    monkeypatch.setattr(audio_player.subprocess, "run", boom)
    audio_player.play_wav(b"x")
