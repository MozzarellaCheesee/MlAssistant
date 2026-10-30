import re
from difflib import SequenceMatcher

_SKIP = {"эй", "окей", "окэй", "ок", "слушай", "привет"}
_FILLER = re.compile(r"а+|(э-?)+|эм+|м+|хм+|ну")
WAKE_MATCH_RATIO = 0.75  # насколько первое слово должно быть похоже на имя
DEFAULT_SESSION_SILENCE = 5.0


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е")).split()


def match_wake(text: str, wake_word: str) -> str | None:
    """Остаток фразы после имени ("" если только имя), None если имени нет."""
    words = _words(text)
    while words and words[0] in _SKIP:
        words.pop(0)
    if not words or SequenceMatcher(None, words[0], " ".join(_words(wake_word))).ratio() < WAKE_MATCH_RATIO:
        return None
    return " ".join(words[1:])


def strip_fillers(text: str) -> str:
    return " ".join(word for word in text.split() if not _FILLER.fullmatch(word.lower().strip(",.!?…;:-")))


class WakeSession:
    """Разговор по имени: имя открывает его, дальше команды принимаются без имени, пока тишина < session_silence."""

    def __init__(self, wake_word: str, session_silence: float = DEFAULT_SESSION_SILENCE):
        self.wake_word = wake_word
        self.session_silence = session_silence
        self.open = False
        self.name_only = False
        self.last = 0.0

    def on_speech(self, now: float) -> None:
        if self.open:
            self.last = now

    def on_transcript(self, text: str, now: float) -> str | None:
        """Команда для выполнения; "Слушаю" при одном имени (name_only=True); None - фразу игнорировать."""
        self.name_only = False
        if self.open and now - self.last > self.session_silence:
            self.open = False
        text = strip_fillers(text.strip())
        if not text:
            return None
        if (rest := match_wake(text, self.wake_word)) is not None:
            text = rest
        elif not self.open:
            return None
        self.open = True
        self.last = now
        if not text:
            self.name_only = True
            return "Слушаю"
        return text
