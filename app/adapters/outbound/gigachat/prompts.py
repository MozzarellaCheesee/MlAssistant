"""Схемы функций, примеры и системный промпт для GigaChat. Только данные: разбор ответа -- в intent_parser.py.

Любая правка здесь меняет то, что видит модель; tests/unit/test_prompt_snapshot.py ловит случайные изменения.
"""

from gigachat.models import Function, FunctionParameters


def _string(description: str) -> dict:
    return {"type": "string", "description": description}


KIND_PROPERTY = {"type": "string", "description": "Тип записи", "enum": ["task", "meeting", "reminder"]}
TARGET_PROPS = {  # о какой существующей записи речь
    "target_ref": {"type": "string", "description": "«её», «его», «эту» -- last", "enum": ["last"]},
    "target_query": _string("Слова из названия записи, о которой речь"),
}
DATE_CODE_RULES = (
    "today; +Nd/+Nw/+Nm (дни/недели/месяцы от сегодня); mon..sun (ближайший такой день после сегодня); "
    "next mon..sun (день следующей недели); eow (конец недели = воскресенье); eom (конец месяца); "
    "MM-DD (месяц-день); YYYY-MM-DD. "
    "«Завтра» -> +1d, «послезавтра» -> +2d, «через неделю» -> +1w, «через три недели» -> +3w, "
    "«через месяц» -> +1m, «в пятницу» -> fri, "
    "«в следующую среду», «в среду на следующей неделе» -> next wed, «до конца недели» -> eow, "
    "«15 декабря» -> 12-15. Никогда не считай дату сам"
)
PERIOD_PROPERTY = _string(
    "Период кодом: week (эта неделя), next week, month (до конца месяца), next month, "
    "один день (today, +1d, fri, 12-15) "
    "или A..B из двух дат («с понедельника по среду» -> mon..wed). «Завтра» -> +1d, «в следующем месяце» -> next month"
)
DATE_TIME_PROPS = {  # дата и время новой записи
    "date": _string("Дата кодом: " + DATE_CODE_RULES + ". Время не названо -- заполняй только date"),
    "time": _string(
        "Время H:MM или HH:MM, только если названо; не выдумывай. "
        "«В это время», «в это же время», «на то же время» -- время и дата записи из последних записей, "
        "time_said -- эти слова. Передавай как сказал пользователь, "
        "невозможное («24:30», «29 часов», «25:70», «-3 часа») не исправляй и не выдумывай, "
        "передай как есть («24:30», «29:00», «25:70», «-3:00»). "
        "Переводи только 12-часовые формы: «3 дня» -> 15:00, «10 вечера» -> 22:00. "
        "Час без «утра/вечера»: 8-11 -- утро, 1-7 -- прибавь 12 часов"
    ),
}
SAID_PROPS = {  # дословные цитаты: по ним код проверяет, что время и дата не выдуманы
    "time_said": _string(
        "Дословный кусок фразы, где названо время («в 10 утра», «в полночь»); время не названо -- пусто"
    ),
    "date_said": _string(
        "Дословный кусок фразы, где назван день («завтра», «в пятницу», «до конца недели»); день не назван -- пусто"
    ),
}
RRULE_PROPS = {
    "rrule": _string(
        "Правило повтора RFC 5545: FREQ=…;INTERVAL=…;BYDAY=…;BYMONTHDAY=…. "
        "Для «каждый/каждую», «по будням», «ежедневно» (например FREQ=WEEKLY;BYDAY=MO); "
        "порядковые правила вроде «каждый второй вторник» не поддерживаются -- не заполняй"
    )
}
SHIFT_PROPS = {
    "shift_minutes": {
        "type": "integer",
        "description": "Только для «через N минут/часов» -- число минут; "
        "«через день/неделю/месяц» сюда НЕ класть, это date (+1d, +1w, +1m). date при этом не заполняй",
    }
}
EXISTING_ITEM_PROPS = {  # уточнения, чтобы найти существующую запись
    "date": _string("Дата существующей записи, если названа, кодом: " + DATE_CODE_RULES + ". Не угадывай"),
    "kind": KIND_PROPERTY,
}
SCOPE_PROPS = {
    "scope": {
        "type": "string",
        "description": "one -- только ближайшее повторение («только эту», «ближайшую»), "
        "all -- вся серия («все повторы», «всю серию»)",
        "enum": ["one", "all"],
    }
}

# Примеры по функциям. Порядок -- часть промпта: модель видит их в этом порядке
EXAMPLES: dict[str, list[dict]] = {
    "create_item": [
        {
            "request": "напомни завтра в 10 утра позвонить Ивану",
            "params": {
                "kind": "reminder",
                "title": "позвонить Ивану",
                "date": "+1d",
                "time": "10:00",
                "time_said": "в 10 утра",
                "date_said": "завтра",
            },
        },
        {
            "request": "Напомни завтра в 24:30 выпить воды",
            "params": {
                "kind": "reminder",
                "title": "выпить воды",
                "date": "+1d",
                "time": "24:30",
                "time_said": "в 24:30",
                "date_said": "завтра",
            },
        },
        {
            "request": "Напомни завтра в 29 часов позвонить маме",
            "params": {
                "kind": "reminder",
                "title": "позвонить маме",
                "date": "+1d",
                "time": "29:00",
                "time_said": "в 29 часов",
                "date_said": "завтра",
            },
        },
        {
            "request": "созвон с командой в пятницу в 15 на час",
            "params": {
                "kind": "meeting",
                "title": "созвон с командой",
                "date": "fri",
                "time": "15:00",
                "duration_minutes": 60,
                "time_said": "в 15",
                "date_said": "в пятницу",
            },
        },
        {
            "request": "Поставь встречу завтра в 24:30",
            "params": {
                "kind": "meeting",
                "title": "встреча",
                "date": "+1d",
                "time": "24:30",
                "time_said": "в 24:30",
                "date_said": "завтра",
            },
        },
        {
            "request": "Встреча с командой завтра в 3 дня",
            "params": {
                "kind": "meeting",
                "title": "встреча с командой",
                "date": "+1d",
                "time": "15:00",
                "time_said": "в 3 дня",
                "date_said": "завтра",
            },
        },
        {
            "request": "в пятницу отправить отчёт",
            "params": {"kind": "task", "title": "отправить отчёт", "date": "fri", "date_said": "в пятницу"},
        },
        {
            "request": "напоминай каждый день в 8 утра пить витамины",
            "params": {
                "kind": "reminder",
                "title": "пить витамины",
                "date": "+1d",
                "time": "08:00",
                "rrule": "FREQ=DAILY",
                "time_said": "в 8 утра",
                "date_said": "каждый день",
            },
        },
        {
            "request": "Напомни мне завтра сестре цветы купить",
            "params": {"kind": "reminder", "title": "купить сестре цветы", "date": "+1d", "date_said": "завтра"},
        },
        {
            "request": "Напомни сегодня в 18 выпить воды",
            "params": {
                "kind": "reminder",
                "title": "выпить воды",
                "date": "today",
                "time": "18:00",
                "time_said": "в 18",
                "date_said": "сегодня",
            },
        },
        # далёкие даты -- кодом, не числом
        {
            "request": "Поставь встречу с Иваном через месяц в 12",
            "params": {
                "kind": "meeting",
                "title": "встреча с Иваном",
                "date": "+1m",
                "time": "12:00",
                "date_said": "через месяц",
                "time_said": "в 12",
            },
        },
        {
            "request": "Напомни 15 декабря в 10 оплатить налог",
            "params": {
                "kind": "reminder",
                "title": "оплатить налог",
                "date": "12-15",
                "time": "10:00",
                "date_said": "15 декабря",
                "time_said": "в 10",
            },
        },
        {
            "request": "через три недели сдать проект",
            "params": {"kind": "task", "title": "сдать проект", "date": "+3w", "date_said": "через три недели"},
        },
        {
            "request": "в среду на следующей неделе созвон в 11",
            "params": {
                "kind": "meeting",
                "title": "созвон",
                "date": "next wed",
                "time": "11:00",
                "date_said": "в среду на следующей неделе",
                "time_said": "в 11",
            },
        },
        {
            "request": "Встреча с Иваном через неделю в 15",
            "params": {
                "kind": "meeting",
                "title": "встреча с Иваном",
                "date": "+1w",
                "time": "15:00",
                "date_said": "через неделю",
                "time_said": "в 15",
            },
        },
        {
            "request": "Каждый понедельник в 9 планёрка",
            "params": {
                "kind": "meeting",
                "title": "планёрка",
                "date": "mon",
                "time": "09:00",
                "rrule": "FREQ=WEEKLY;BYDAY=MO",
                "time_said": "в 9",
                "date_said": "Каждый понедельник",
            },
        },
        {
            "request": "Поставь встречу с Иваном завтра",
            "params": {"kind": "meeting", "title": "встреча с Иваном", "date": "+1d", "date_said": "завтра"},
        },
        {
            "request": "в 17 (в ответ на «На какое время поставить встречу?», черновик create_item kind=meeting)",
            "params": {"kind": "meeting", "time": "17:00", "time_said": "в 17"},
        },
        # «через N часов» -- shift_minutes, дату и время считает код
        {
            "request": "Напомни через 30 часов позвонить маме",
            "params": {"kind": "reminder", "title": "позвонить маме", "shift_minutes": 1800},
        },
        {
            "request": "Через два часа напомни выпить воды",
            "params": {"kind": "reminder", "title": "выпить воды", "shift_minutes": 120},
        },
        {
            "request": "Встреча с командой завтра в 10 на 2 часа",
            "params": {
                "kind": "meeting",
                "title": "встреча с командой",
                "date": "+1d",
                "time": "10:00",
                "duration_minutes": 120,
                "time_said": "в 10",
                "date_said": "завтра",
            },
        },
        {
            "request": "Купить билеты в театр до конца недели",
            "params": {"kind": "task", "title": "купить билеты в театр", "date": "eow", "date_said": "до конца недели"},
        },
        {
            "request": "Поставь задачу купить билеты до конца недели",
            "params": {"kind": "task", "title": "купить билеты", "date": "eow", "date_said": "до конца недели"},
        },
        {
            "request": "Надо подготовить договор к понедельнику",
            "params": {"kind": "task", "title": "подготовить договор", "date": "mon", "date_said": "к понедельнику"},
        },
        {
            "request": "сделай мне короче завтра встречу с иннокентием в 10 00 утра",
            "params": {
                "kind": "meeting",
                "title": "встреча с Иннокентием",
                "date": "+1d",
                "time": "10:00",
                "time_said": "в 10 00 утра",
                "date_said": "завтра",
            },
        },
        # день недели -- ближайший такой день после сегодня, по календарю
        {
            "request": "напомни в субботу в 10 полить цветы",
            "params": {
                "kind": "reminder",
                "title": "полить цветы",
                "date": "sat",
                "time": "10:00",
                "date_said": "в субботу",
                "time_said": "в 10",
            },
        },
        {
            "request": "в среду созвон с командой в 11",
            "params": {
                "kind": "meeting",
                "title": "созвон с командой",
                "date": "wed",
                "time": "11:00",
                "date_said": "в среду",
                "time_said": "в 11",
            },
        },
        {
            "request": "во вторник сдать отчёт",
            "params": {"kind": "task", "title": "сдать отчёт", "date": "tue", "date_said": "во вторник"},
        },
        {
            "request": "в четверг встреча с юристом в 16",
            "params": {
                "kind": "meeting",
                "title": "встреча с юристом",
                "date": "thu",
                "time": "16:00",
                "date_said": "в четверг",
                "time_said": "в 16",
            },
        },
        # ссылки на последнюю запись: смысл отмечает модель, дату и время считает код
        {
            "request": "добавь в это же время созвон с Ниной",
            "params": {"kind": "meeting", "title": "созвон с Ниной", "same_time": True},
        },
        {
            "request": "напомни за час до этой встречи взять документы",
            "params": {"kind": "reminder", "title": "взять документы", "offset_minutes": -60},
        },
        {
            "request": "через полчаса после неё поставь встречу с Олегом",
            "params": {"kind": "meeting", "title": "встреча с Олегом", "offset_minutes": 30},
        },
        {
            "request": "на следующий день в то же время поставь встречу с Ниной",
            "params": {"offset_days": 1, "same_time": True, "kind": "meeting", "title": "встреча с Ниной"},
        },
        {
            "request": "на следующий день в 9 напомни позвонить",
            "params": {"kind": "reminder", "title": "позвонить", "offset_days": 1, "time": "9:00", "time_said": "в 9"},
        },
        # «этого же дня» -- день последней записи, не сегодня
        {
            "request": "поставь на 15:00 этого же дня созвон с Ниной",
            "params": {
                "kind": "meeting",
                "title": "созвон с Ниной",
                "same_day": True,
                "time": "15:00",
                "time_said": "15:00",
            },
        },
        # название -- суть дела, без «что мне», «нужно»
        {
            "request": "напомни завтра в 12, что мне на пары",
            "params": {
                "kind": "reminder",
                "title": "на пары",
                "date": "+1d",
                "time": "12:00",
                "date_said": "завтра",
                "time_said": "в 12",
            },
        },
        # перечень внутри одной задачи -- одна команда
        {
            "request": "Купить хлеб, молоко и яйца завтра",
            "params": {"kind": "task", "title": "купить хлеб, молоко и яйца", "date": "+1d", "date_said": "завтра"},
        },
    ],
    "list_items": [
        {"request": "что у меня на завтра", "params": {"period": "+1d"}},
        {"request": "что у меня в следующем месяце", "params": {"period": "next month"}},
        {"request": "Какие задачи у меня на завтра?", "params": {"kind": "task", "period": "+1d"}},
        {"request": "что на этой неделе", "params": {"period": "week"}},
        {"request": "что на следующей неделе", "params": {"period": "next week"}},
        {"request": "Есть ли у меня встречи сегодня?", "params": {"kind": "meeting", "period": "today"}},
    ],
    "search_items": [
        {
            "request": "когда у меня была встреча про бюджет",
            "params": {"query": "бюджет", "kind": "meeting", "tense": "past"},
        },
        {
            "request": "Когда у меня была встреча с Иваном?",
            "params": {"query": "Иван", "kind": "meeting", "tense": "past"},
        },
        {
            "request": "Когда я завтра должен встретиться с Ваньком?",
            "params": {"query": "Иван", "kind": "meeting", "period": "+1d"},
        },
        {"request": "Я когда-то ставил задачу по договору, найди её", "params": {"query": "договор", "kind": "task"}},
    ],
    "complete_item": [
        {"request": "отметь её выполненной", "params": {"target_ref": "last"}},
    ],
    "update_item": [
        {"request": "перенеси её на 16", "params": {"target_ref": "last", "new_time": "16:00"}},
        {
            "request": "эту же задачу завтра напомни мне в 15",
            "params": {"target_ref": "last", "remind": True, "new_date": "+1d", "new_time": "15:00"},
        },
        # сдвиг от самой записи
        {"request": "перенеси её на час позже", "params": {"target_ref": "last", "delay_minutes": 60}},
        {"request": "сдвинь планёрку на полчаса раньше", "params": {"target_query": "планёрка", "delay_minutes": -30}},
        {"request": "перенеси её на следующий день", "params": {"target_ref": "last", "delay_days": 1}},
        {"request": "перенеси её на месяц", "params": {"target_ref": "last", "delay_months": 1}},
        {"request": "перенеси её на 15 декабря", "params": {"target_ref": "last", "new_date": "12-15"}},
        {"request": "перенеси её на неделю позже", "params": {"target_ref": "last", "delay_days": 7}},
        {
            "request": "перенеси встречу с Олегом на день раньше",
            "params": {"target_query": "Олег", "kind": "meeting", "delay_days": -1},
        },
        {
            "request": "Переименуй задачу про отчёт в квартальный отчёт",
            "params": {"target_query": "отчёт", "title": "квартальный отчёт"},
        },
        {
            "request": "перенеси встречу на пятницу на 11",
            "params": {"target_query": "встреча", "new_date": "fri", "new_time": "11:00"},
        },
    ],
    "delete_item": [
        {"request": "удали задачу про отчёт", "params": {"target_query": "отчёт"}},
        {
            "request": "встречу завтра убери с иннокентием",
            "params": {"target_query": "Иннокентий", "kind": "meeting", "date": "+1d"},
        },
    ],
    "confirm": [
        {"request": "да", "params": {"answer": "yes"}},
    ],
}


def _function(name: str, description: str, properties: dict, required=()) -> dict:
    parameters = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = list(required)
    return {"name": name, "description": description, "parameters": parameters, "few_shot_examples": EXAMPLES[name]}


FUNCTIONS: list[dict] = [
    _function(
        "create_item",
        "НОВАЯ запись: задача, напоминание или встреча. Тип выбери в kind. "
        "«Сделай/оформи/запланируй/поставь/добавь … встречу» и «напомни» -- тоже создание",
        {
            "kind": {
                "type": "string",
                "enum": ["task", "reminder", "meeting"],
                "description": "meeting — встреча/созвон с людьми в определённое время; "
                "reminder — «напомни», «не забыть»; "
                "task — дело, которое нужно сделать (купить, отправить, подготовить…)",
            },
            "title": _string(
                "Название словами пользователя в его порядке, без смены падежей («купить маме подарок»), "
                "без обращений и ругательств. Разговорные имена оставляй как сказал пользователь («встреча с Ванёчком»)"
            ),
            **DATE_TIME_PROPS,
            **SAID_PROPS,
            "duration_minutes": {
                "type": "integer",
                "description": "Только для meeting: длительность в минутах, «на 2 часа» -- duration_minutes=120",
            },
            "offset_days": {
                "type": "integer",
                "description": "Сдвиг в днях от дня последней записи: «на следующий день», «назавтра после неё» 1, "
                "«за день до неё» -1; date тогда не заполняй; вместе с same_time -- тот же час, но другой день",
            },
            "same_day": {
                "type": "boolean",
                "description": "true, если день назван ссылкой на последнюю запись "
                "(«этого же дня», «в тот же день», «на этот же день»); date тогда не заполняй",
            },
            "same_time": {
                "type": "boolean",
                "description": "true, если время названо ссылкой на последнюю запись "
                "(«в это же время», «на то же время», «в это время»); time и date тогда не заполняй",
            },
            "offset_minutes": {
                "type": "integer",
                "description": "Сдвиг от времени последней записи: «за час до неё» -60, «через полчаса после неё» 30; "
                "time и date тогда не заполняй",
            },
            **RRULE_PROPS,
            **SHIFT_PROPS,
        },
        required=["kind"],
    ),
    _function(
        "list_items",
        "Показать записи за период (сегодня/завтра/неделя), можно с типом",
        {"period": PERIOD_PROPERTY, "kind": KIND_PROPERTY},
    ),
    _function(
        "search_items",
        "Найти уже существующую запись по словам: названию/человеку/теме, в том числе в прошлом "
        "(только если просят найти или спрашивают «когда», «была ли»)",
        {
            "query": _string(
                "Только имена и темы в словарной форме, разговорные имена -- полными "
                "(Ванёк, Ваня -> Иван; Маша -> Мария; Лёша -> Алексей). "
                "Слово «встреча/задача» не включай, оно идёт в kind"
            ),
            "kind": KIND_PROPERTY,
            "period": PERIOD_PROPERTY,
            "tense": {"type": "string", "description": "Прошлое/будущее", "enum": ["past", "future", "any"]},
        },
        required=["query"],
    ),
    _function(
        "complete_item",
        "Отметить выполненной СУЩЕСТВУЮЩУЮ запись",
        {**TARGET_PROPS, **EXISTING_ITEM_PROPS, **SCOPE_PROPS},
    ),
    _function(
        "update_item",
        "Изменить или перенести СУЩЕСТВУЮЩУЮ запись",
        {
            **TARGET_PROPS,
            "title": _string(
                "Новое название, только если пользователь его назвал; "
                "вопросы вроде «напиши, с кем встреча» -- это не название"
            ),
            "new_time": _string("Новое время HH:MM, только если названо"),
            "new_date": _string("Новая дата кодом (как в date), только если названа; дату записи не угадывай"),
            "delay_minutes": {
                "type": "integer",
                "description": "Сдвиг самой записи по времени: «на час позже» 60, «на полчаса раньше» -30; "
                "new_time тогда не заполняй",
            },
            "delay_days": {
                "type": "integer",
                "description": "Сдвиг самой записи по дням: «на день позже», «на следующий день» 1, "
                "«на неделю позже» 7, «на день раньше» -1; new_date тогда не заполняй",
            },
            "delay_months": {
                "type": "integer",
                "description": "Сдвиг самой записи по месяцам: «на месяц позже» 1; new_date тогда не заполняй",
            },
            "remind": {
                "type": "boolean",
                "description": "true, если пользователь просит напомнить об уже существующей записи "
                "(«эту же задачу напомни в 15»); время напоминания -- new_time",
            },
            **EXISTING_ITEM_PROPS,
            **SHIFT_PROPS,
            **SCOPE_PROPS,
        },
    ),
    _function(
        "delete_item",
        "Удалить СУЩЕСТВУЮЩУЮ запись: убрать, стереть, отменить, вычеркнуть, снести -- в любой формулировке "
        "(только если есть такая просьба)",
        {**TARGET_PROPS, **EXISTING_ITEM_PROPS, **SCOPE_PROPS},
    ),
    _function(
        "confirm",
        "Короткий ответ «да»/«нет» на вопрос ассистента, только когда есть ожидаемый вопрос; "
        "фраза с делом или «напомни» -- не confirm",
        {"answer": {"type": "string", "description": "yes или no", "enum": ["yes", "no"]}},
        required=["answer"],
    ),
]

# то же, что FUNCTIONS, в виде объектов SDK для Chat(functions=...)
SDK_FUNCTIONS = [
    Function(
        name=f["name"],
        description=f["description"],
        parameters=FunctionParameters(**f["parameters"]),
        few_shot_examples=f["few_shot_examples"],
    )
    for f in FUNCTIONS
]

SPLIT = Function(
    name="split_commands",
    description="Разбей фразу пользователя голосового ассистента на отдельные команды "
    "(создать, удалить, перенести, отметить, показать, найти). "
    "Команды могут быть связаны чем угодно: «и», запятая, «потом», «затем», «после этого», «а ещё», «также», «плюс», "
    "новое предложение. "
    "Каждую команду верни самостоятельной фразой, «её», «эту», «в это же время» замени конкретным названием, "
    "датой и временем. "
    "Перечень внутри одной задачи («купить хлеб, молоко и яйца», "
    "«встреча с Иваном и Петром») -- одна команда. Если команда одна, верни её как есть.",
    parameters=FunctionParameters(
        type="object", properties={"commands": {"type": "array", "items": {"type": "string"}}}, required=["commands"]
    ),
    few_shot_examples=[
        {
            "request": "Перенеси созвон на 12. После этого покажи планы на неделю",
            "params": {"commands": ["перенеси созвон на 12", "покажи планы на неделю"]},
        },
        {
            "request": "создай встречу с Димой в среду в 18 и перенеси её на 19",
            "params": {"commands": ["создай встречу с Димой в среду в 18", "перенеси встречу с Димой в среду на 19"]},
        },
        {
            "request": "Поставь встречу с Олегом в пятницу в 15 и на этот же день в 11 созвон с Ниной",
            "params": {"commands": ["Поставь встречу с Олегом в пятницу в 15", "на этот же день в 11 созвон с Ниной"]},
        },
        {
            "request": "Запиши встречу с юристом завтра в 10, а за час до неё напомни взять паспорт",
            "params": {"commands": ["Запиши встречу с юристом завтра в 10", "за час до неё напомни взять паспорт"]},
        },
        {
            "request": "Создай задачу отчёт и удали её, а потом поставь в это время звонок маме",
            "params": {"commands": ["Создай задачу отчёт", "удали её", "поставь в это время звонок маме"]},
        },
        {
            "request": "напомни завтра в 9 про лекцию и в 13 обед с Олей, послезавтра нужно купить корм",
            "params": {
                "commands": ["напомни завтра в 9 про лекцию", "в 13 обед с Олей", "послезавтра нужно купить корм"]
            },
        },
        {"request": "Купить хлеб, молоко и яйца завтра", "params": {"commands": ["Купить хлеб, молоко и яйца завтра"]}},
        {"request": "что у меня завтра", "params": {"commands": ["что у меня завтра"]}},
        {"request": "Нет, отмена", "params": {"commands": ["Нет, отмена"]}},
        {"request": "да, давай, ставь", "params": {"commands": ["да, давай, ставь"]}},
    ],
)

SYSTEM_PROMPT = (
    "Ты разбираешь реплики пользователя голосового ассистента задач, встреч и напоминаний. Вызови ровно одну функцию.\n"
    "Сейчас: {now}.\n"
    "- Если ассистент задал вопрос (pending), ответ пользователя («в 17», «завтра», новое время "
    "на «Добавить всё равно?») "
    "-- вызови ТУ ЖЕ функцию и тот же kind, что в pending, только с новыми полями, не update_item и не confirm. "
    "Ответ без времени и даты («не знаю», «как хочешь») -- оставь все аргументы пустыми.\n"
    "- «да», «нет» -> confirm. Числа и время («14:25», «пять») -- НЕ да и НЕ нет.\n"
    "- Вопросы к самому ассистенту и болтовня («как дела?», «кто ты?») -> unknown, не confirm.\n"
    "Ожидается ответ на вопрос: {pending}\n"
    "Последние записи: {recent}"
)
