import re

_U = [
    "ноль",
    "один",
    "два",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
]
_T = ["_", "_", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_ORD_U = [
    "_",
    "первое",
    "второе",
    "третье",
    "четвёртое",
    "пятое",
    "шестое",
    "седьмое",
    "восьмое",
    "девятое",
    "десятое",
    "одиннадцатое",
    "двенадцатое",
    "тринадцатое",
    "четырнадцатое",
    "пятнадцатое",
    "шестнадцатое",
    "семнадцатое",
    "восемнадцатое",
    "девятнадцатое",
]
_ORD_T = {20: "двадцатое", 30: "тридцатое"}
MAX_SPOKEN_NUMBER = 100
MAX_HOUR = 23
MAX_MINUTE = 59
MAX_DAY_OF_MONTH = 31
_MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря"


def _cardinal(n: int) -> str:
    if n == MAX_SPOKEN_NUMBER:
        return "сто"
    if n < 20:
        return _U[n]
    return _T[n // 10] + (f" {_U[n % 10]}" if n % 10 else "")


def _ordinal(n: int) -> str:
    if n < 20:
        return _ORD_U[n]
    return _ORD_T[n] if n % 10 == 0 else f"{_T[n // 10]} {_ORD_U[n % 10]}"


def _time(match: re.Match) -> str:
    hours, mins = int(match[1]), int(match[2])
    if hours > MAX_HOUR or mins > MAX_MINUTE:
        return match[0]
    if mins == 0:
        minutes = "ноль-ноль"
    elif mins < 10:
        minutes = f"ноль {_U[mins]}"
    else:
        minutes = _cardinal(mins)
    return f"{_cardinal(hours)} {minutes}"


def _day_of_month(match: re.Match) -> str:
    day = int(match[1])
    return _ordinal(day) if 1 <= day <= MAX_DAY_OF_MONTH else match[0]


def _number(match: re.Match) -> str:
    number = int(match[0])
    return _cardinal(number) if number <= MAX_SPOKEN_NUMBER else match[0]


def normalize_for_speech(text: str) -> str:
    """Время, дата и целые 0-100 словами: синтезатор плохо читает цифры.

    Именительный падеж, без согласования.
    """
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _time, text)
    text = re.sub(
        rf"\b(\d{{1,2}})(?= ({_MONTHS.replace(' ', '|')})\b)",
        _day_of_month,
        text,
    )
    return re.sub(r"(?<![\d.,:])\d{1,3}(?![\d.,:]|\d)", _number, text)
