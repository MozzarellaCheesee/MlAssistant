import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    gigachat_credentials: str
    gigachat_scope: str
    gigachat_model_pro: str
    gigachat_model_lite: str
    db_path: str
    stt_model: str
    certs_path: str = "certs/gigachat.pem"
    auth_disabled: bool = False
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    yandex_redirect_uri: str = ""
    cookie_secure: bool = True
    demo_seed: bool = False
    token_encryption_key: str = ""
    wake_word: str = "Джарвис"
    voice_enabled: bool = False
    voice_user_id: str = "default"
    voice_phrase_pause: float = 1.0
    voice_session_silence: float = 5.0
    tts_model: str = "vosk-model-tts-ru-0.9-multi"
    tts_speaker: int = 4
    tts_preload: bool = True
    voice_sounds: bool = True
    voice_volume: float = 0.8
    voice_beep_volume: float = 0.5
    voice_debug: bool = False
    dialog_pending_ttl: float = 600


def load_settings() -> Settings:
    load_dotenv()
    env = os.environ
    for name in ("GIGACHAT_CREDENTIALS", "GIGACHAT_MODEL_PRO", "GIGACHAT_MODEL_LITE"):
        if not env.get(name):
            raise RuntimeError(f"{name} is not set")
    disabled = env.get("AUTH_DISABLED") == "1"
    if not disabled:
        for name in ("YANDEX_CLIENT_ID", "YANDEX_CLIENT_SECRET", "YANDEX_REDIRECT_URI"):
            if not env.get(name):
                raise RuntimeError(f"{name} is not set")
    return Settings(
        env["GIGACHAT_CREDENTIALS"],
        env.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        env["GIGACHAT_MODEL_PRO"],
        env["GIGACHAT_MODEL_LITE"],
        env.get("DB_PATH", "./assistant.db"),
        env.get("STT_MODEL", "gigaam-v3-e2e-rnnt"),
        auth_disabled=disabled,
        yandex_client_id=env.get("YANDEX_CLIENT_ID", ""),
        yandex_client_secret=env.get("YANDEX_CLIENT_SECRET", ""),
        yandex_redirect_uri=env.get("YANDEX_REDIRECT_URI", ""),
        cookie_secure=env.get("COOKIE_SECURE", "1") != "0",
        demo_seed=env.get("DEMO_SEED", "0") == "1",
        wake_word=env.get("WAKE_WORD", "Джарвис"),
        voice_enabled=env.get("VOICE_ENABLED") == "1",
        voice_user_id=env.get("VOICE_USER_ID", "default"),
        voice_phrase_pause=float(env.get("VOICE_PHRASE_PAUSE_SECONDS", "1.0")),
        voice_session_silence=float(env.get("VOICE_SESSION_SILENCE_SECONDS", "5")),
        tts_model=env.get("TTS_MODEL", "vosk-model-tts-ru-0.9-multi"),
        tts_speaker=int(env.get("TTS_SPEAKER", "4")),
        tts_preload=env.get("TTS_PRELOAD", "1") != "0",
        voice_sounds=env.get("VOICE_SOUNDS", "1") != "0",
        voice_debug=env.get("VOICE_DEBUG", "0") == "1",
        dialog_pending_ttl=float(env.get("DIALOG_PENDING_TTL_SECONDS", "600")),
        voice_volume=min(1.0, max(0.0, float(env.get("VOICE_VOLUME", "0.8")))),
        voice_beep_volume=min(1.0, max(0.0, float(env.get("VOICE_BEEP_VOLUME", "0.5")))),
        token_encryption_key=env.get("TOKEN_ENCRYPTION_KEY", ""),
    )
