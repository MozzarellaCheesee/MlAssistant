import logging
import threading
from datetime import timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from app.adapters.inbound.voice_loop import VoiceControl, VoiceLoop
from app.adapters.observability import Logged
from app.adapters.outbound.audio_player import play_wav
from app.adapters.outbound.e5_embedder import E5Embedder
from app.adapters.outbound.gigaam_stt import GigaAMSpeechToText
from app.adapters.outbound.gigachat.client import make_client
from app.adapters.outbound.gigachat.intent_parser import GigaChatIntentParser
from app.adapters.outbound.sqlite.calendar_links import SqliteCalendarLinks
from app.adapters.outbound.sqlite.db import Database
from app.adapters.outbound.sqlite.dialog_store import SqliteDialogStore
from app.adapters.outbound.sqlite.item_repo import SqliteItemRepository
from app.adapters.outbound.sqlite.message_log import SqliteMessageLog
from app.adapters.outbound.sqlite.user_store import SqliteUserStore
from app.adapters.outbound.sqlite.vector_index import SqliteVectorIndex
from app.adapters.outbound.system_clock import SystemClock
from app.adapters.outbound.yandex_calendar.caldav import YandexCalendarSync
from app.adapters.outbound.yandex_id import YandexIdProvider
from app.application.assistant import AssistantService
from app.application.auth import AuthService
from app.application.handlers.registry import build_handlers
from app.application.ports.outbound import CalendarLink, CalendarLinks, CalendarSync, SpeechToText
from app.config import Settings

log = logging.getLogger(__name__)
DEFAULT_TOKEN_TTL_S = 365 * 24 * 3600  # срок токена, если Яндекс не вернул expires_in


def build_calendar(settings: Settings, db: Database) -> tuple[CalendarLinks, CalendarSync] | tuple[None, None]:
    """Выгрузка включена, если задан ключ и вход не отключён."""
    if not settings.token_encryption_key or settings.auth_disabled:
        return None, None
    try:
        Fernet(settings.token_encryption_key)
    except Exception:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is invalid") from None
    links = SqliteCalendarLinks(db, settings.token_encryption_key.encode())
    sync = YandexCalendarSync(links, settings.yandex_client_id, settings.yandex_client_secret, SystemClock())
    return links, Logged(sync, "calendar", {"push", "remove"})


class LazyTTS:
    """Один движок Vosk на цикл Джарвиса и страницу; модель грузится при первой озвучке."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._tts = None
        self._lock = threading.Lock()
        self._warm = False

    @property
    def ready(self) -> bool:
        return self._tts is not None

    def warmup(self) -> None:
        """Фоновая загрузка модели; повторный вызов ничего не делает, сбой виден при озвучке/включении."""
        if self._warm:
            return
        self._warm = True

        def run():
            try:
                self.load()
            except Exception:
                log.exception("tts warmup failed")

        threading.Thread(target=run, daemon=True, name="tts-warmup").start()

    def load(self):
        with self._lock:
            if self._tts is None:
                from app.adapters.outbound.vosk_tts import VoskTTS  # тяжёлый импорт

                self._tts = VoskTTS.load(self._settings.tts_model, self._settings.tts_speaker)
            return self._tts

    def synthesize(self, text: str) -> bytes:
        return self.load().synthesize(text)  # движок сам сериализует вызовы


def build_voice(settings: Settings, assistant: AssistantService, tts: LazyTTS | None = None) -> VoiceControl:
    tts = tts or LazyTTS(settings)

    def play_main(wav: bytes) -> None:
        play_wav(wav, volume=ctl.volume)

    def play_beep(wav: bytes) -> None:
        play_wav(wav, volume=ctl.beep_volume)

    def make() -> VoiceLoop:
        tts.load()  # сбой загрузки модели виден при включении
        return VoiceLoop(
            assistant,
            assistant.stt,
            tts,
            settings.voice_user_id,
            settings.wake_word,
            settings.voice_phrase_pause,
            settings.voice_session_silence,
            player=play_main,
            beeper=play_beep,
            sounds=settings.voice_sounds,
            debug=settings.voice_debug,
        )

    ctl = VoiceControl(
        make,
        settings.wake_word,
        owner=settings.voice_user_id,
        volume=settings.voice_volume,
        beep_volume=settings.voice_beep_volume,
        phrase_pause=settings.voice_phrase_pause,
        session_silence=settings.voice_session_silence,
    )
    return ctl


def build_auth(
    settings: Settings, db: Database, embedder: E5Embedder | None = None, links: CalendarLinks | None = None
) -> AuthService | None:
    if settings.auth_disabled:
        return None
    hook = None
    if settings.demo_seed:

        def hook(user):
            from scripts.import_csv import import_csv

            import_csv(
                SqliteItemRepository(db, embedder or E5Embedder.load()),
                db,
                str(Path(__file__).resolve().parents[1] / "data/voice_assistant_events_1000.csv"),
                user_id=user.id,
            )

    idp = YandexIdProvider(settings.yandex_client_id, settings.yandex_client_secret, settings.yandex_redirect_uri)
    on_login = None
    if links is not None:
        clock = SystemClock()

        def on_login(user, profile):
            if profile.email:
                expires_in = DEFAULT_TOKEN_TTL_S if profile.expires_in is None else profile.expires_in
                links.save(
                    CalendarLink(
                        user.id,
                        profile.email,
                        profile.access_token,
                        profile.refresh_token,
                        clock.now() + timedelta(seconds=expires_in),
                    )
                )
            else:
                log.warning("calendar not linked user=%s: no email from Yandex (grant login:email)", user.id)

    return AuthService(idp, SqliteUserStore(db), SystemClock(), hook, on_login)


def build(
    settings: Settings,
    stt: SpeechToText | None = None,
    db: Database | None = None,
    embedder: E5Embedder | None = None,
    calendar: CalendarSync | None = None,
) -> AssistantService:
    db = db or Database(settings.db_path)
    embedder = embedder or E5Embedder.load()
    clock = SystemClock()
    repo = SqliteItemRepository(db, embedder, clock)
    repo.embed_missing()
    pro = make_client(
        settings.gigachat_credentials, settings.gigachat_scope, settings.gigachat_model_pro, settings.certs_path
    )
    search = SqliteVectorIndex(db, embedder)
    handlers = build_handlers(repo, search, calendar)
    if stt is None:
        stt = GigaAMSpeechToText.load(settings.stt_model)
    lite = make_client(
        settings.gigachat_credentials, settings.gigachat_scope, settings.gigachat_model_lite, settings.certs_path
    )
    parser = Logged(GigaChatIntentParser(pro, split_client=lite), "llm", {"parse"})
    return AssistantService(
        parser,
        Logged(stt, "stt", {"transcribe"}),
        repo,
        SqliteDialogStore(db, clock.now, settings.dialog_pending_ttl),
        clock,
        handlers,
        SqliteMessageLog(db, clock),
        calendar,
    )
