from datetime import UTC, datetime, timedelta

from app.domain.dates import MSK
from app.domain.item import Item, ItemKind

FIRST_LINE_BYTES = 75  # RFC 5545: строка не длиннее 75 октетов, продолжения с пробелом -- 74 + пробел
CONTINUATION_BYTES = 74
REMINDER_MINUTES = 15  # длительность события-напоминания
_TZ = "TZID=Europe/Moscow"
_VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:Europe/Moscow",
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0300",
    "TZOFFSETTO:+0300",
    "TZNAME:MSK",
    "END:STANDARD",
    "END:VTIMEZONE",
]


def _esc(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    parts, current, size = [], "", 0
    for char in line:
        char_size = len(char.encode())
        if size + char_size > (FIRST_LINE_BYTES if not parts else CONTINUATION_BYTES):
            parts.append(current)
            current, size = "", 0
        current, size = current + char, size + char_size
    return "\r\n ".join(parts + [current])


def _f(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%S")


def build_ics(item: Item, uid: str, now: datetime) -> str:
    if item.starts_at is None:
        raise ValueError("item has no starts_at")
    start = item.starts_at.astimezone(MSK)
    end = (
        start + timedelta(minutes=REMINDER_MINUTES)
        if item.kind == ItemKind.REMINDER
        else item.effective_end().astimezone(MSK)
    )
    title = _esc(item.title)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ml-assistant//RU",
        *_VTIMEZONE,
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now.astimezone(UTC):%Y%m%dT%H%M%SZ}",
        f"DTSTART;{_TZ}:{_f(start)}",
        f"DTEND;{_TZ}:{_f(end)}",
        f"SUMMARY:{title}",
    ]
    if item.rrule:
        lines.append(f"RRULE:{item.rrule}")
    if item.exdates:
        lines.append(f"EXDATE;{_TZ}:" + ",".join(_f(datetime.combine(day, start.time())) for day in item.exdates))
    if item.kind == ItemKind.REMINDER:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:PT0S", f"DESCRIPTION:{title}", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "".join(_fold(line) + "\r\n" for line in lines)
