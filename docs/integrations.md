# Исходящие адаптеры

Справочник по `app/adapters/outbound/`. Адаптеры реализуют порты из `app/application/ports/outbound.py`. Домен и сценарии знают только порты. Конкретные классы создаёт `app/bootstrap.py`.

Все пути ниже даны от корня репозитория. Ссылки `путь:строка` указывают на текущую версию кода. Причины решений взяты из комментариев в коде, `README.md` (разделы «Решения и причины», «TLS и сертификаты», «Поиск»). Если причина нигде не записана, документ это не скрывает.

## 1. Какой адаптер реализует какой порт

Порты описаны через `typing.Protocol`, поэтому наследование не обязательно. Явно наследуют порт только `GigaChatIntentParser`, `GigaAMSpeechToText` и `VoskTTS`. Остальные классы подходят по структуре.

| Порт | Адаптер | Файл |
|---|---|---|
| `IntentParser` | `GigaChatIntentParser` | `app/adapters/outbound/gigachat/intent_parser.py:119` |
| `Embedder` | `E5Embedder` | `app/adapters/outbound/e5_embedder.py:6` |
| `SpeechToText` | `GigaAMSpeechToText` | `app/adapters/outbound/gigaam_stt.py:16` |
| `TextToSpeech` | `VoskTTS` | `app/adapters/outbound/vosk_tts.py:12` |
| `ItemRepository` | `SqliteItemRepository` | `app/adapters/outbound/sqlite/item_repo.py:69` |
| `SearchIndex` | `SqliteVectorIndex` | `app/adapters/outbound/sqlite/vector_index.py:61` |
| `DialogStore` | `SqliteDialogStore` | `app/adapters/outbound/sqlite/dialog_store.py:13` |
| `MessageLog` | `SqliteMessageLog` | `app/adapters/outbound/sqlite/message_log.py:16` |
| `UserStore` | `SqliteUserStore` | `app/adapters/outbound/sqlite/user_store.py:8` |
| `CalendarLinks` | `SqliteCalendarLinks` | `app/adapters/outbound/sqlite/calendar_links.py:12` |
| `Clock` | `SystemClock` | `app/adapters/outbound/system_clock.py:6` |
| `IdentityProvider` | `YandexIdProvider` | `app/adapters/outbound/yandex_id.py:11` |
| `CalendarSync` | `YandexCalendarSync` | `app/adapters/outbound/yandex_calendar/caldav.py:22` |

Модули без порта:

- `gigachat/client.py` строит клиент GigaChat, ограничивает TLS и разбирает ошибки. Его использует `GigaChatIntentParser`.
- `audio_player.py` проигрывает WAV и генерирует сигналы. Его использует голосовой цикл.
- `sqlite/db.py` хранит схему и общее соединение. Его используют все SQLite-адаптеры.
- `yandex_calendar/ics.py` строит текст события iCalendar для `YandexCalendarSync`.

Расхождения с портами:

- `SqliteItemRepository.add` принимает дополнительный аргумент `embed` ([item_repo.py:122](../app/adapters/outbound/sqlite/item_repo.py)), порт его не знает.
- `SqliteItemRepository.embed_missing` отсутствует в порту. Его вызывают при старте и при импорте.
- `list_between` в порту требует `kind` и `status`, в адаптере у них есть значения по умолчанию `None`.

Ошибки внешних систем адаптеры превращают в `AssistantError` с кодом `ErrorCode` (`app/application/errors.py`). Коды, которые используют адаптеры: `LLM_UNAVAILABLE`, `LLM_QUOTA`, `LLM_RATE_LIMIT`, `CERT_MISMATCH`, `STT_FAILED`, `AUDIO_TOO_LONG`, `AUTH_FAILED`, `CALENDAR_FAILED`.

## 2. GigaChat: клиент (`gigachat/client.py`)

Модуль создаёт клиент SDK `gigachat.GigaChat` с закреплённым сертификатом и переводит исключения SDK в `AssistantError`.

### 2.1. Константы

- `API_URL = "https://gigachat.devices.sberbank.ru/api/v1"` ([client.py:15](../app/adapters/outbound/gigachat/client.py)). Адрес API передаётся в клиент явно. Адрес по умолчанию из SDK (`api.giga.chat`) не используется (README, «Технологии»).
- `_FORBIDDEN_ENV` ([client.py:16](../app/adapters/outbound/gigachat/client.py)). Переменные окружения `GIGACHAT_CA_BUNDLE_FILE`, `GIGACHAT_VERIFY_SSL_CERTS`, `GIGACHAT_BASE_URL`, `GIGACHAT_AUTH_URL`. Они переопределяют настройки клиента и ломают закрепление сертификата.

### 2.2. `build_ssl_context(pem_path) -> ssl.SSLContext` ([client.py:19](../app/adapters/outbound/gigachat/client.py))

Параметр: путь к файлу `certs/gigachat.pem`. Возвращает контекст TLS для клиента.

Шаги:

1. Создаёт `SSLContext(PROTOCOL_TLS_CLIENT)`. Системные корневые сертификаты не загружаются, доверие дают только сертификаты из файла.
2. Загружает файл через `load_verify_locations`.
3. Добавляет флаг `VERIFY_X509_PARTIAL_CHAIN`. С ним листовой сертификат из файла сам служит якорем доверия и корневой сертификат не нужен.
4. Включает `check_hostname = True` и `verify_mode = CERT_REQUIRED`.

Причина (README, «TLS и сертификаты» и «Безопасность и вход»): серверы GigaChat подписаны «Russian Trusted Sub CA» Минцифры. Корневой сертификат Минцифры не устанавливается: он дал бы доверие ко всем сайтам, которые он подписывает. `verify_ssl_certs=False` не используется: отключённая проверка открыла бы подмену ответа. В файле лежат два листовых сертификата (`ngw.devices.sberbank.ru:9443` и `gigachat.devices.sberbank.ru:443`). Они истекают 30.06.2027 и 13.07.2027. Обновление: `python -m scripts.pin_certs`. Контекст применяется только клиентом GigaChat, другие сайты им отклоняются (README проверял на ya.ru).

### 2.3. `make_client(credentials, scope, model, pem_path, timeout=20) -> GigaChat` ([client.py:28](../app/adapters/outbound/gigachat/client.py))

Параметры: ключ авторизации, область доступа (`scope`), имя модели, путь к PEM-файлу, таймаут запроса в секундах.

Шаги:

1. Ищет в `os.environ` запрещённые переменные из `_FORBIDDEN_ENV`.
2. Если нашёл, бросает `RuntimeError` со списком имён и просьбой удалить их из окружения.
3. Иначе возвращает `GigaChat(..., ssl_context=build_ssl_context(pem_path), timeout=timeout, base_url=API_URL)`.

`bootstrap.py` вызывает функцию дважды: для модели Pro (разбор команд) и для модели Lite (деление фразы). Эти вызовы расположены в `app/bootstrap.py:177` и `app/bootstrap.py:184`.

### 2.4. `call_errors()` ([client.py:45](../app/adapters/outbound/gigachat/client.py))

Контекстный менеджер. Переводит исключения в `AssistantError`. Порядок проверок:

| Условие | Результат |
|---|---|
| `AssistantError` | пробрасывается без изменений |
| `ssl.SSLCertVerificationError` или `CERTIFICATE_VERIFY_FAILED` в тексте ошибки | `CERT_MISMATCH`, подробность `run python -m scripts.pin_certs` |
| `ResponseError` со `status_code == 402` | `LLM_QUOTA` |
| `GigaChatException`, `httpx.HTTPError`, `TimeoutError` | `LLM_UNAVAILABLE` |
| любое другое | пробрасывается как есть |

Во всех переводах исходное исключение сохраняется в `__cause__`. Ответ 429 здесь не обрабатывается: он попадает в `LLM_UNAVAILABLE`, а `chat_retry` достаёт из `__cause__` исходный статус.

### 2.5. `chat_retry(client, payload, sleep=time.sleep)` ([client.py:60](../app/adapters/outbound/gigachat/client.py))

Вызывает `client.chat(payload)` и повторяет запрос при ответе 429.

Шаги:

1. Перебирает паузы `(1, 2, None)`, то есть делает до трёх попыток.
2. Каждую попытку выполняет внутри `call_errors()`.
3. При успехе возвращает ответ.
4. Если исключение не 429 (проверяется `__cause__` у `AssistantError` или само исключение), пробрасывает его.
5. При 429 и паузе `None` (третья попытка) бросает `AssistantError(LLM_RATE_LIMIT)`.
6. При 429 с числовой паузой вызывает `sleep(delay)` и пробует снова.

Параметр `sleep` нужен тестам: они подставляют пустую функцию. Причина повторов: личный тариф GigaChat отвечает 429 на второй одновременный запрос (README, «Составные фразы» и «Ограничения»).

## 3. GigaChat: разбор намерений (`gigachat/intent_parser.py`, схемы и примеры — `gigachat/prompts.py`)

Адаптер превращает фразу пользователя в объект `Intent`. Работает в два шага: деление на команды, затем разбор одной команды через function calling. Модель не определяет даты: она возвращает выражения на коротком языке (DSL), а даты считает код.

### 3.1. Принципы (README, «Понимание запроса»)

- Смысл фразы определяет модель, списков ключевых слов нет. Слова-маркеры ломались на перефразировании и ошибочно срабатывали на ответах на уточняющие вопросы. Это также требование заказчика.
- Одна функция `create_item` с полем `kind` вместо трёх функций. С тремя функциями модель путала тип: «Купить билеты в театр до конца недели» и «Надо подготовить договор к понедельнику» становились встречами. Результат live-eval вырос с 43 до 45 из 46.
- Правила лежат в описаниях полей и функций, системный промпт короткий. С длинным блоком правил модель на «Напомни купить молоко» и «Купить подарок маме» выбирала удаление или отметку о выполнении. После переноса правил в схему eval стал 46 из 46.
- Модель передаёт время так, как его сказали, а проверяет его код. Модель молча «исправляла» невозможное время («29 часов», «24:30»).
- Даты считает код. Модель видела календарь на 14 дней и дальше ошибалась: «через три недели», «в следующем месяце» давали неверный день. Перевести фразу в `+1m` или `next wed` проще, чем сложить дни.
- В примерах схемы нет конкретных дат. Раньше примеры были написаны от 28 октября, модель копировала из них даты.

### 3.2. Константы и блоки схемы

Схемы функций, примеры и системный промпт лежат отдельно от разбора, в `gigachat/prompts.py`. Модуль содержит только данные. Тест `tests/unit/test_prompt_snapshot.py` сравнивает JSON всех функций, `SPLIT` и `SYSTEM_PROMPT` со снимком `tests/unit/snapshots/gigachat_prompts.json`: случайная правка текста, который видит модель, роняет тест. Осознанная правка перезаписывает снимок командой `python -m tests.unit.test_prompt_snapshot`.

В `intent_parser.py`:

- `log`, `WEEKDAYS_RU` ([intent_parser.py:20](../app/adapters/outbound/gigachat/intent_parser.py)). Русские названия дней недели от понедельника. Подставляются в строку «Сейчас» системного промпта.
- `PARSE_ATTEMPTS` ([:19](../app/adapters/outbound/gigachat/intent_parser.py)). Две попытки разбора, раздел 3.9.

В `prompts.py`:

- `_string(description)` ([prompts.py:9](../app/adapters/outbound/gigachat/prompts.py)). Строит описание строкового поля `{"type": "string", "description": description}`.
- `KIND_PROPERTY` ([:13](../app/adapters/outbound/gigachat/prompts.py)). Поле «Тип записи», значения `task`, `meeting`, `reminder`.
- `TARGET_PROPS` ([:14](../app/adapters/outbound/gigachat/prompts.py)). Цель команды: `target_ref` (единственное значение `last`, для «её», «его», «эту») и `target_query` (слова из названия).
- `DATE_CODE_RULES` ([:18](../app/adapters/outbound/gigachat/prompts.py)). Текст-описание языка дат для модели. Разбор — в разделе 3.4.
- `PERIOD_PROPERTY` ([:27](../app/adapters/outbound/gigachat/prompts.py)). Описание поля периода.
- `DATE_TIME_PROPS` ([:32](../app/adapters/outbound/gigachat/prompts.py)). Поля `date` и `time` новой записи.
- `SAID_PROPS` ([:44](../app/adapters/outbound/gigachat/prompts.py)). Поля `time_said` и `date_said`.
- `RRULE_PROPS` ([:52](../app/adapters/outbound/gigachat/prompts.py)). Поле `rrule`.
- `SHIFT_PROPS` ([:59](../app/adapters/outbound/gigachat/prompts.py)). Поле `shift_minutes`.
- `EXISTING_ITEM_PROPS` ([:66](../app/adapters/outbound/gigachat/prompts.py)). Поля `date` существующей записи и `kind`.
- `SCOPE_PROPS` ([:70](../app/adapters/outbound/gigachat/prompts.py)). Поле `scope` (`one` или `all`).
- `EXAMPLES` ([:80](../app/adapters/outbound/gigachat/prompts.py)). Примеры `request` → `params`, сгруппированные по имени функции. Порядок примеров — часть промпта. Большинство примеров у `create_item`. В примерах `create_item` заполнены `time_said` и `date_said`, чтобы модель училась цитировать.
- `_function(name, description, properties, required=())` ([:446](../app/adapters/outbound/gigachat/prompts.py)). Собирает словарь функции: `name`, `description`, `parameters` (JSON Schema `object`, `required` только если он не пуст) и `few_shot_examples` из `EXAMPLES[name]`.
- `FUNCTIONS` ([:453](../app/adapters/outbound/gigachat/prompts.py)). Список из семи функций по порядку: `create_item`, `list_items`, `search_items`, `complete_item`, `update_item`, `delete_item`, `confirm`.
- `SDK_FUNCTIONS` ([:578](../app/adapters/outbound/gigachat/prompts.py)). `FUNCTIONS`, преобразованные в объекты `gigachat.models.Function` с `few_shot_examples`.
- `SPLIT` ([:588](../app/adapters/outbound/gigachat/prompts.py)). Функция `split_commands`, раздел 3.6.
- `SYSTEM_PROMPT` ([:635](../app/adapters/outbound/gigachat/prompts.py)). Раздел 3.5.

### 3.3. Функции, которые видит модель

Модель получает все семь функций в `Chat(functions=SDK_FUNCTIONS, function_call="auto")` и вызывает одну. Ниже — все поля. Тип `string`, если не указано иное.

#### `create_item`: новая запись

Описание функции: новая запись (задача, напоминание или встреча), тип выбирается в `kind`. Фразы «сделай/оформи/запланируй/поставь/добавь … встречу» и «напомни» тоже означают создание. Обязательное поле: `kind`.

| Поле | Тип | Что передаёт модель |
|---|---|---|
| `kind` | enum `task`, `reminder`, `meeting` | `meeting` — встреча или созвон с людьми в определённое время; `reminder` — «напомни», «не забыть»; `task` — дело, которое нужно сделать |
| `title` | string | Название словами пользователя в его порядке, без смены падежей, без обращений и ругательств. Разговорные имена остаются как сказаны («встреча с Ванёчком») |
| `date` | string | Дата выражением DSL. Если время не названо, заполняется только `date` |
| `time` | string | `H:MM` или `HH:MM`, только если названо. «В это время» — время и дата из последних записей, при этом `time_said` — эти слова. Невозможное время («24:30», «29 часов», «25:70», «-3 часа») передаётся как есть, без исправления («24:30», «29:00», «25:70», «-3:00»). Переводятся только 12-часовые формы («3 дня» → 15:00, «10 вечера» → 22:00). Час без «утра» и «вечера»: 8–11 — утро, 1–7 — прибавить 12 часов |
| `time_said` | string | Дословный кусок фразы со временем («в 10 утра», «в полночь»). Время не названо — пусто |
| `date_said` | string | Дословный кусок фразы с днём («завтра», «в пятницу», «до конца недели»). День не назван — пусто |
| `duration_minutes` | integer | Только для `meeting`: длительность в минутах («на 2 часа» → 120) |
| `offset_days` | integer | Сдвиг в днях от дня последней записи: «на следующий день» 1, «за день до неё» -1. `date` не заполняется. Вместе с `same_time` даёт тот же час в другой день |
| `same_day` | boolean | `true`, если день назван ссылкой на последнюю запись («этого же дня», «в тот же день»). `date` не заполняется |
| `same_time` | boolean | `true`, если время названо ссылкой на последнюю запись («в это же время»). `time` и `date` не заполняются |
| `offset_minutes` | integer | Сдвиг от времени последней записи: «за час до неё» -60, «через полчаса после неё» 30. `time` и `date` не заполняются |
| `rrule` | string | Правило повтора RFC 5545 (`FREQ=…;INTERVAL=…;BYDAY=…;BYMONTHDAY=…`) для «каждый/каждую», «по будням», «ежедневно». Порядковые правила («каждый второй вторник») не поддерживаются — поле не заполняется |
| `shift_minutes` | integer | Только для «через N минут/часов» — число минут. «Через день/неделю/месяц» сюда не попадает, это `date` (`+1d`, `+1w`, `+1m`). `date` не заполняется |

Примеры (`few_shot_examples`) охватывают: напоминание с датой и временем, невозможное время (24:30, 29 часов), встречу с длительностью, задачу без времени, повтор («каждый день», «каждый понедельник»), даты кодом (`+1m`, `12-15`, `+3w`, `next wed`, `eow`, дни недели), ссылки на последнюю запись (`same_time`, `same_day`, `offset_minutes`, `offset_days`), сдвиг `shift_minutes` («через 30 часов» → 1800, «через два часа» → 120), ответ на вопрос о времени, название без слов «что мне», перечень внутри одной задачи.

#### `list_items`: показать записи за период

| Поле | Тип | Что передаёт модель |
|---|---|---|
| `period` | string | Период выражением: `week`, `next week`, `month`, `next month`, один день (`today`, `+1d`, `fri`, `12-15`) или `A..B` («с понедельника по среду» → `mon..wed`) |
| `kind` | enum | Тип записи |

#### `search_items`: найти существующую запись

Описание: найти существующую запись по словам, в том числе в прошлом. Вызывается, только если просят найти или спрашивают «когда», «была ли». Обязательное поле: `query`.

| Поле | Тип | Что передаёт модель |
|---|---|---|
| `query` | string | Только имена и темы в словарной форме. Разговорные имена полностью (Ванёк, Ваня → Иван; Маша → Мария; Лёша → Алексей). Слово «встреча» или «задача» не включается: оно идёт в `kind` |
| `kind` | enum | Тип записи |
| `period` | string | Период, как в `list_items` |
| `tense` | enum `past`, `future`, `any` | Прошлое, будущее или любое |

#### `complete_item`: отметить выполненной

Поля: `TARGET_PROPS` (`target_ref`, `target_query`), `EXISTING_ITEM_PROPS` (`date`, `kind`), `SCOPE_PROPS` (`scope`).

- `target_ref` — только `last`.
- `target_query` — слова из названия записи.
- `date` — дата существующей записи кодом, если названа. Не угадывать.
- `kind` — тип записи.
- `scope` — `one` (только ближайшее повторение: «только эту», «ближайшую») или `all` (вся серия: «все повторы», «всю серию»).

#### `update_item`: изменить или перенести

Поля: `TARGET_PROPS`, `EXISTING_ITEM_PROPS`, `SHIFT_PROPS`, `SCOPE_PROPS` и собственные.

| Поле | Тип | Что передаёт модель |
|---|---|---|
| `target_ref`, `target_query` | см. выше | Цель |
| `title` | string | Новое название, только если пользователь его назвал. Вопрос «напиши, с кем встреча» — не название |
| `new_time` | string | Новое время `HH:MM`, только если названо |
| `new_date` | string | Новая дата выражением DSL, только если названа. Дату записи не угадывать |
| `delay_minutes` | integer | Сдвиг записи по времени: «на час позже» 60, «на полчаса раньше» -30. `new_time` не заполняется |
| `delay_days` | integer | Сдвиг по дням: «на день позже», «на следующий день» 1, «на неделю позже» 7, «на день раньше» -1. `new_date` не заполняется |
| `delay_months` | integer | Сдвиг по месяцам: «на месяц позже» 1. `new_date` не заполняется |
| `remind` | boolean | `true`, если просят напомнить об уже существующей записи («эту же задачу напомни в 15»). Время напоминания — `new_time` |
| `date` | string | Дата существующей записи (из `EXISTING_ITEM_PROPS`) |
| `kind` | enum | Тип записи (из `EXISTING_ITEM_PROPS`) |
| `shift_minutes` | integer | Как в `create_item` (из `SHIFT_PROPS`) |
| `scope` | enum `one`, `all` | Как в `complete_item` |

Причина полей `delay_*`: раньше «на следующий день» отсчитывалось от сегодня, а «на час позже» давало несуществующее время; теперь модель отдаёт сдвиг, а код считает от времени записи.

#### `delete_item`: удалить

Описание: удалить существующую запись — убрать, стереть, отменить, вычеркнуть, снести в любой формулировке, только если есть такая просьба. Поля те же, что у `complete_item`: `target_ref`, `target_query`, `date`, `kind`, `scope`.

#### `confirm`: ответ «да» или «нет»

Описание: короткий ответ «да» или «нет» на вопрос ассистента, только если есть ожидаемый вопрос. Фраза с делом или «напомни» — не `confirm`. Обязательное поле: `answer` (enum `yes`, `no`).

### 3.4. Язык выражений дат (DSL)

Функции разбора находятся в `app/domain/dates.py`. Адаптер вызывает их из `_dsl`. Обе функции приводят строку к нижнему регистру, обрезают пробелы и возвращают `None` для непонятного выражения.

`parse_date_expr(expr, today) -> date | None` ([dates.py:63](../app/domain/dates.py))

| Выражение | Результат |
|---|---|
| `today` | сегодня |
| `+Nd`, `+Nw`, `+Nm` (знак обязателен, допустим и `-`) | сегодня плюс N дней, N недель (N × 7 дней) или N месяцев |
| `mon`…`sun` | ближайший такой день строго после сегодня. Если сегодня тот же день недели, берётся через 7 дней |
| `next mon`…`next sun` | этот день недели следующей календарной недели |
| `eow` | воскресенье текущей недели (если сегодня воскресенье, то сегодня) |
| `eom` | последний день текущего месяца |
| `MM-DD` | ближайшая такая дата не раньше сегодня: сначала этого года, затем следующего. Если первое число больше 12, оно считается днём, а второе месяцем («15-12») |
| `YYYY-MM-DD` | эта дата |

Мусор, несуществующая дата (например, `02-30`) и переполнение дают `None`. `+Nm` считает `add_months` ([dates.py:41](../app/domain/dates.py)): день обрезается до конца месяца (31 января + 1 месяц = конец февраля).

`parse_period_expr(expr, today) -> tuple[date, date] | None` ([dates.py:92](../app/domain/dates.py))

| Выражение | Результат |
|---|---|
| `week` | от сегодня до `eow` |
| `next week` | понедельник следующей недели и шесть дней после него |
| `month` | от сегодня до `eom` |
| `next month` | первый и последний день следующего месяца |
| `A..B` | от даты `A` до даты `B`, обе по `parse_date_expr` |
| один день | `(d, d)` |

Если у `A..B` вторая часть пуста или одна из частей не разобралась, результат `None`. Порядок `A` и `B` не проверяется.

Что DSL не умеет (README, «Ограничения»): день недели плюс сдвиг («в пятницу через три недели»).

### 3.5. Системный промпт

`SYSTEM_PROMPT` ([prompts.py:635](../app/adapters/outbound/gigachat/prompts.py)) короткий: правила лежат в схеме. Подстановки: `{now}`, `{pending}`, `{recent}`. Содержание:

- Модель вызывает ровно одну функцию.
- Если ассистент задал вопрос (`pending`), ответ пользователя («в 17», «завтра», новое время на «Добавить всё равно?») вызывает ту же функцию с тем же `kind`, что в `pending`, только с новыми полями. Это не `update_item` и не `confirm`. Ответ без времени и даты («не знаю», «как хочешь») оставляет все аргументы пустыми.
- «Да» и «нет» — `confirm`. Числа и время («14:25», «пять») не считаются ни «да», ни «нет».
- Вопросы к самому ассистенту и болтовня («как дела?», «кто ты?») — `unknown`, не `confirm`. Отдельной функции `unknown` нет: если модель ничего не вызвала, срабатывает повтор (раздел 3.9).

Причины (README, «Диалог»): «да» считается подтверждением, только если ассистент действительно задал такой вопрос. На «почему голос женский?» ассистент отвечал «Хорошо.», потому что модель принимала фразу за подтверждение. Число не бывает «да» или «нет»: это время.

### 3.6. Деление на команды

#### `SPLIT` ([prompts.py:588](../app/adapters/outbound/gigachat/prompts.py))

Функция `split_commands` с одним обязательным полем `commands` (массив строк). Описание просит разбить фразу на отдельные команды (создать, удалить, перенести, отметить, показать, найти), даже если они связаны «и», запятой, «потом», «затем», «после этого», «а ещё», «также», «плюс» или новым предложением. Каждая команда возвращается самостоятельной фразой, а «её», «эту», «в это же время» заменяются конкретным названием, датой и временем. Перечень внутри одной задачи («купить хлеб, молоко и яйца», «встреча с Иваном и Петром») — одна команда. Если команда одна, она возвращается как есть. В примерах есть и случаи, когда деления не должно быть («Нет, отмена», «да, давай, ставь»).

Причины (README, «Составные фразы»):

- В обычном разборе модель возвращала остаток фразы, только если команды были соединены «и», а на запятых, «потом», «после этого» теряла вторую команду. Отдельный запрос с одной задачей делит правильно: 38 из 38 на 19 фразах по два прогона.
- Делить по словам «и» и по запятым в коде нельзя: «и» бывает частью названия.
- Делит лёгкая модель GigaChat-2: точность та же (19 из 19), время 0,34 с против 0,71 с у Pro. В коде это `split_client` ([intent_parser.py:120](../app/adapters/outbound/gigachat/intent_parser.py)).

#### `GigaChatIntentParser.__init__(client, sleep=time.sleep, split_client=None)` ([:120](../app/adapters/outbound/gigachat/intent_parser.py))

Сохраняет клиент для разбора, функцию паузы для `chat_retry` и клиент для деления. Если `split_client` не передан, деление идёт тем же клиентом.

#### `_split(text, ctx) -> list[str]` ([:126](../app/adapters/outbound/gigachat/intent_parser.py))

1. Собирает строку последних записей `название (когда)` через `format_when`.
2. Формирует сообщения: фраза пользователя. Если `ctx.recent` не пуст, добавляет второе пользовательское сообщение «(Последние записи: …)», чтобы «в это время» и «её» стали конкретными.
3. Вызывает `chat_retry` с `Chat(functions=[SPLIT], function_call={"name": "split_commands"}, temperature=0.1)`. Функция вызывается принудительно.
4. Берёт `commands` из `function_call.arguments`.
5. `AssistantError` пробрасывает. Любое другое исключение логирует предупреждением (`split failed`) и возвращает `[text]`.
6. Если `commands` — список, возвращает только непустые строки. Иначе возвращает `[text]`.

Результат: пустой список возможен, если все элементы пустые. В `parse` пустой и одноэлементный список ведут к обычному разбору.

#### `parse(text, ctx) -> Intent` ([:149](../app/adapters/outbound/gigachat/intent_parser.py))

1. Если `ctx.split` ложно или есть `ctx.pending`, сразу вызывает `_parse`. Деление отключено для подкоманд и для ответов на вопрос ассистента.
2. Иначе вызывает `_split`.
3. Если команд больше одной, возвращает `Several(commands=cmds)`. Иначе вызывает `_parse` для исходного текста.

Деление и разбор идут по очереди, не параллельно. Причина (код и README): личный тариф GigaChat отвечает 429 на второй одновременный запрос, параллельный ход занимал 3,2–4,4 с.

### 3.7. Вспомогательные функции разбора

#### `_pending_view(pending) -> dict` ([:23](../app/adapters/outbound/gigachat/intent_parser.py))

Параметр: черновик с вопросом ассистента (словарь). Убирает ключи со значением `None`. Если `intent` равен `create_task` или `create_meeting`, подменяет его на `create_item` и ставит `kind` (`meeting` для встречи, иначе исходный `kind` или `task`). Причина (комментарий в коде): модель видит одну функцию `create_item`.

#### `_dsl(args, today) -> None` ([:31](../app/adapters/outbound/gigachat/intent_parser.py))

Переводит выражения дат в ISO. Меняет `args` на месте.

1. Для `date` и `new_date`: если значение непустое, убирает его из `args`, разбирает `parse_date_expr`. Если разобралось, кладёт обратно ISO-строку. Если нет, поле остаётся удалённым. Ассистент потом переспросит.
2. Если есть `period`, убирает его и разбирает `parse_period_expr`. При успехе записывает `date_from` и `date_to` в ISO.

#### `_norm(text) -> str` ([:46](../app/adapters/outbound/gigachat/intent_parser.py))

Нормализация для сравнения цитат: нижний регистр, `ё` → `е`, всё кроме букв, цифр, `_` и `:` заменяется пробелом, пробелы схлопываются.

#### `_relative(args, last) -> None` ([:50](../app/adapters/outbound/gigachat/intent_parser.py))

Считает дату и время для ссылок на последнюю запись. Параметр `last` — запись `ctx.recent[0]` или `None`.

1. Удаляет из `args` поля `same_day`, `same_time`, `offset_minutes`, `offset_days`. Пустые значения считаются нулём.
2. Если `last` нет или ни один признак не задан, возвращает управление. Поля уже удалены, модель не видит их в результате.
3. Удаляет `shift_minutes`: ссылка на запись важнее сдвига.
4. Если задан `same_time` или `offset_minutes` и у `last` есть `starts_at`, берёт `starts_at + offset_days + offset_minutes` и записывает `date` и `time` (`HH:MM`). Прежние `date` и `time` перезаписываются.
5. Иначе, если у `last` есть день (`day()`), записывает `date = day + offset_days`. Время остаётся тем, что назвала модель («на следующий день в 9»).

Причина (README): модель сама ставила «этого же дня» на сегодня, а «через час после неё» считала от текущего момента. Отметить смысл ей проще, чем посчитать дату. На 15 таких фразах было 5 верных, стало 14. Последний случай упёрся в конфликт с другой встречей.

#### `_hour_said(hhmm, said) -> bool` ([:65](../app/adapters/outbound/gigachat/intent_parser.py))

Проверяет, что час из значения времени есть во фразе числом. Берёт часть до `:`. Если это не число, возвращает `False`. Иначе сравнивает цифры из `said` с двумя вариантами: час как есть и час по 12-часовым часам (`h % 12 or 12`). Для 18:00 подойдёт «18» и «6».

#### `_ground(text, args, recent=()) -> None` ([:72](../app/adapters/outbound/gigachat/intent_parser.py))

Отбрасывает время и дату, которых нет во фразе. Меняет `args` на месте.

1. `said = _norm(text)`.
2. Строит множество известных значений из `recent`: времена `HH:MM` из `starts_at` и даты ISO из `day()`.
3. Для пары `time`/`time_said`, затем `date`/`date_said`:
   - Если это `date` и в `args` есть `rrule`, убирает `date_said` и пропускает проверку. У серии дата — первое повторение, во фразе её может не быть.
   - Достаёт и удаляет цитату `*_said`, нормализует.
   - Для времени дополняет значение нулями слева до 5 символов (`9:00` → `09:00`).
   - Для времени без цитаты, но с часом, названным числом (`_hour_said`), оставляет значение.
   - Если значение есть, а цитаты нет или она не входит в `said` как подстрока, и значение не равно известному из `recent`, удаляет его из `args`.

Причина (README): на «Напомни завтра позвонить в банк» модель сама ставила 00:00 или 23:59, и ни описание поля, ни примеры её не останавливали. Значение остаётся и без цитаты, если час числом есть во фразе («в 6 вечера» → 18:00) или если время и дата совпадают с последней записью: модель иногда забывает цитату, а настоящее время терять нельзя. Для ответа на вопрос ассистента проверка отключена: там пользователь называет время сам, а цитату модель не возвращает (`_parse` в таком случае удаляет `time_said` и `date_said`).

### 3.8. `_parse(text, ctx) -> Intent` ([:157](../app/adapters/outbound/gigachat/intent_parser.py))

Разбор одной команды. Метод читается как оглавление: системный промпт собирает `_system_prompt` ([:181](../app/adapters/outbound/gigachat/intent_parser.py)), ответ модели в намерение превращает `_to_intent` ([:201](../app/adapters/outbound/gigachat/intent_parser.py)), имя `create_task`/`create_meeting` выбирает `_create_intent_name` ([:93](../app/adapters/outbound/gigachat/intent_parser.py)), сообщение об ошибке для второй попытки строит `_error_message` ([:109](../app/adapters/outbound/gigachat/intent_parser.py)).

Подготовка:

1. `now` — строка вида `2026-10-30T12:00, пятница` (ISO до минут и день недели из `WEEKDAYS_RU`).
2. `recent` — по строке JSON на запись: `id`, `kind`, `title`, `when` (`format_when`). Без записей — «нет».
3. `pending` — JSON `{"question": ctx.pending_question, "intent": _pending_view(ctx.pending)}` или «нет».
4. Сообщения: системное (`SYSTEM_PROMPT`) и пользовательское с текстом фразы.

Попытки (цикл `range(PARSE_ATTEMPTS)`):

1. Вызывает `chat_retry` с `functions=SDK_FUNCTIONS`, `function_call="auto"`, `temperature=0.1`.
2. Берёт `function_call`. Аргументы копирует в словарь `args`.
3. Если вызов есть, логирует `llm <имя> <аргументы>` и применяет `_dsl` с датой `ctx.now.date()`.
4. Если вызов есть и среди значений `args` есть хоть одно не `None` и не пустая строка:
   - Достаёт `target_ref` и `target_query`. Если есть хоть одно, кладёт `args["target"] = {"ref": ..., "query": ...}`. Если названы слова, `ref` равен `None`. Причина (README): модель присылала оба поля сразу, проверка отвергала такой ответ, и «Измени время созвона на 17:00» не разбиралось вовсе.
   - Для `create_item`: при `ctx.pending` удаляет `time_said` и `date_said`, иначе вызывает `_ground`. Затем `_relative` с `ctx.recent[0]` (или `None`). Берёт `kind` (по умолчанию `task`). Выбирает имя `create_meeting` для встречи или `create_task` для остальных. Для не-встречи удаляет `duration_minutes` и возвращает `kind` в `args`.
   - Вызывает `parse_intent({"intent": name, **args})` (`app/application/intents.py`). При `ValidationError` запоминает текст ошибки.
5. Если вызова нет — ошибка «нет вызова функции». Если все аргументы пустые — «все аргументы пустые».
6. При ошибке добавляет в сообщения ответ модели и сообщение с описанием ошибки (`Ошибка: …. Вызови одну из функций с корректными аргументами.`). Если функция была вызвана, это сообщение роли `FUNCTION` с JSON `{"error": …}`. Если не была, обычное пользовательское сообщение. Затем повторяет попытку.
7. После двух неудач возвращает `Unknown()`.

Исключения `AssistantError` из `chat_retry` не перехватываются: они доходят до верхнего уровня.

### 3.9. Повтор при невалидном ответе

Причины повтора: функция не вызвана, все аргументы пустые после `_dsl`, либо `parse_intent` отклонил аргументы (`ValidationError`). Модель получает текст ошибки и одну вторую попытку. Непонятная дата не вызывает повтор сама: `_dsl` просто удаляет поле.

## 4. SQLite

### 4.1. Соединение и схема (`sqlite/db.py`)

`SCHEMA` ([db.py:4](../app/adapters/outbound/sqlite/db.py)) — строка с командами `CREATE TABLE IF NOT EXISTS` и `CREATE INDEX IF NOT EXISTS`. В коде нет миграций: существующая база не получает новые столбцы.

`Database(path)` ([db.py:48](../app/adapters/outbound/sqlite/db.py)):

- Открывает одно соединение `sqlite3.connect(path, check_same_thread=False)`.
- Ставит `row_factory = sqlite3.Row`.
- Создаёт `threading.Lock` в поле `lock`.
- Выполняет `executescript(SCHEMA)`.

Атрибуты `conn` и `lock` используют все SQLite-адаптеры. Почти каждый запрос выполняется внутри `with self.db.lock`. Транзакции на запись идут через `with self.db.conn` (фиксация или откат при выходе из блока).

Причина: комментарий в коде: один connection и глобальный lock, пул нужен при росте нагрузки. README: для прототипа одного пользователя или небольшой команды этого хватает.

Даты и время хранятся в виде ISO-строк (`isoformat()`), сравнения в SQL идут как сравнения строк.

#### Таблица `items`

| Столбец | Тип | Назначение |
|---|---|---|
| `id` | INTEGER, первичный ключ, автоинкремент | Номер записи |
| `user_id` | TEXT, не NULL, по умолчанию `'default'` | Владелец записи |
| `kind` | TEXT, не NULL, одно из `task`, `meeting`, `reminder` | Тип записи |
| `title` | TEXT, не NULL | Название |
| `starts_at` | TEXT | Начало (ISO со смещением) |
| `ends_at` | TEXT | Конец |
| `due_date` | TEXT | Дата записи (`YYYY-MM-DD`). По ней фильтруют списки и поиск |
| `status` | TEXT, не NULL, по умолчанию `open`, одно из `open`, `done`, `cancelled` | Состояние. Удаление ставит `cancelled`, запись остаётся (README, «Диалог») |
| `created_at` | TEXT, не NULL | Время создания |
| `source` | TEXT, не NULL, по умолчанию `user`, одно из `user`, `import` | Источник: ввод пользователя или импорт CSV |
| `source_text` | TEXT | Исходная фраза или строка импорта |
| `notified_at` | TEXT | Время последнего показанного напоминания |
| `rrule` | TEXT | Правило повтора (подмножество RRULE) |
| `exdates` | TEXT | JSON-список пропущенных дат повторения |
| `external_id` | TEXT | UID события в Яндекс Календаре |

Индекс `items_user_due` на `(user_id, due_date)`.

#### Таблица `items_vec`

| Столбец | Тип | Назначение |
|---|---|---|
| `item_id` | INTEGER, первичный ключ | Номер записи из `items` |
| `vec` | BLOB, не NULL | Вектор названия, `float32`, нормированный |

Внешнего ключа нет: связь поддерживает код (`SqliteItemRepository.delete` удаляет строку явно).

#### Таблица `dialog_state`

| Столбец | Тип | Назначение |
|---|---|---|
| `session_id` | TEXT, первичный ключ | Идентификатор сессии диалога |
| `state_json` | TEXT, не NULL | `DialogState` в JSON |
| `updated_at` | TEXT, не NULL | Время последнего сохранения |

#### Таблица `users`

| Столбец | Тип | Назначение |
|---|---|---|
| `id` | TEXT, первичный ключ | Внутренний идентификатор: `"u" + yandex_uid` |
| `yandex_uid` | TEXT, уникальный, не NULL | Идентификатор в Яндексе |
| `login` | TEXT, не NULL | Логин |
| `display_name` | TEXT, не NULL | Отображаемое имя |
| `created_at` | TEXT, не NULL | Время создания |

#### Таблица `auth_sessions`

| Столбец | Тип | Назначение |
|---|---|---|
| `token_hash` | TEXT, первичный ключ | sha256 токена сессии. Хэш считает вызывающий код. Причина (README): по утечке базы сессию не подделать |
| `user_id` | TEXT, не NULL | Владелец сессии |
| `expires_at` | TEXT, не NULL | Срок действия |

#### Таблица `messages`

| Столбец | Тип | Назначение |
|---|---|---|
| `id` | INTEGER, первичный ключ, автоинкремент | Номер сообщения |
| `user_id` | TEXT, не NULL | Владелец |
| `role` | TEXT, одно из `user`, `assistant` | Автор |
| `text` | TEXT, не NULL | Текст |
| `source` | TEXT, одно из `text`, `voice` | Способ ввода |
| `items_json` | TEXT | Записи, приложенные к сообщению, JSON |
| `created_at` | TEXT, не NULL | Время |

Индекс `messages_user` на `(user_id, id)`.

#### Таблица `oauth_states`

| Столбец | Тип | Назначение |
|---|---|---|
| `state` | TEXT, первичный ключ | Одноразовое значение `state` при входе через Яндекс |
| `expires_at` | TEXT, не NULL | Срок действия |

#### Таблица `calendar_links`

| Столбец | Тип | Назначение |
|---|---|---|
| `user_id` | TEXT, первичный ключ | Владелец |
| `email` | TEXT, не NULL | Адрес календаря |
| `token_enc` | TEXT, не NULL | Шифрованный access-токен (Fernet) |
| `refresh_enc` | TEXT | Шифрованный refresh-токен, может отсутствовать |
| `expires_at` | TEXT, не NULL | Время истечения access-токена |

### 4.2. Репозиторий записей (`sqlite/item_repo.py`)

Модульные функции:

- `_iso(v)` ([item_repo.py:17](../app/adapters/outbound/sqlite/item_repo.py)). `v.isoformat()` или `None` для пустого значения.
- `_dt(v)` ([:21](../app/adapters/outbound/sqlite/item_repo.py)). `datetime.fromisoformat(v)` или `None`.
- `_to_row(i) -> dict` ([:25](../app/adapters/outbound/sqlite/item_repo.py)). Превращает `Item` в словарь столбцов `items` (без `id`). Если `due_date` пуст, берёт дату `starts_at` в часовом поясе MSK. `exdates` сериализует в JSON-список ISO-дат.
- `_from_row(r) -> Item` ([:45](../app/adapters/outbound/sqlite/item_repo.py)). Обратное преобразование. Используется и в `vector_index.py`.

#### `SqliteItemRepository(db, embedder, clock=None)` ([:69](../app/adapters/outbound/sqlite/item_repo.py))

Если `clock` не передан, берёт `SystemClock()`.

- `_vec(title) -> bytes` ([:75](../app/adapters/outbound/sqlite/item_repo.py)). Считает вектор названия `embed_passages([title])[0]` и пакует в `float32`.
- `embed_missing(batch=64)` ([:78](../app/adapters/outbound/sqlite/item_repo.py)). Находит записи без строки в `items_vec` и считает векторы порциями по `batch`. Нужна для импорта и для существующих баз. README: недостающие векторы досчитываются при старте.
- `_query(sql, args) -> list[Item]` ([:93](../app/adapters/outbound/sqlite/item_repo.py)). Выполняет запрос под блокировкой и преобразует строки.
- `add(item, embed=True) -> Item` ([:122](../app/adapters/outbound/sqlite/item_repo.py)). Если `created_at` пуст, ставит время часов. Вектор считается до записи в базу (вне блокировки). Затем в одной транзакции вставляет запись и, если `embed` истинно, вектор. Записывает `item.id` и возвращает ту же запись. Импорт (`scripts/import_csv.py`) передаёт `embed=False` и потом вызывает `embed_missing`.
- `get(user_id, item_id) -> Item | None` ([:128](../app/adapters/outbound/sqlite/item_repo.py)). Выборка с проверкой владельца.
- `update(item) -> None` ([:132](../app/adapters/outbound/sqlite/item_repo.py)). Перезаписывает все столбцы по паре `id` и `user_id`. Вектор пересчитывает только если название изменилось. Если ни одна строка не обновилась, вектор не пишется.
- `detach_occurrence(series, occurrence) -> Item` ([:137](../app/adapters/outbound/sqlite/item_repo.py)). Обновляет серию (с новым `exdate`) и вставляет отдельную запись для повторения одной транзакцией: при сбое повторение не теряется. Общий код вставки и обновления под уже взятой блокировкой — `_insert` ([:98](../app/adapters/outbound/sqlite/item_repo.py)) и `_update` ([:108](../app/adapters/outbound/sqlite/item_repo.py)); `_new_vec` ([:117](../app/adapters/outbound/sqlite/item_repo.py)) считает вектор, только если сменилось название.
- `delete(user_id, item_id) -> None` ([:145](../app/adapters/outbound/sqlite/item_repo.py)). В одной транзакции удаляет вектор, затем запись. Это физическое удаление; логика статуса `cancelled` находится выше, в сценариях.
- `list_between(user_id, rng, kind=None, status=None) -> list[Item]` ([:153](../app/adapters/outbound/sqlite/item_repo.py)). Записи в диапазоне дат.
  1. SQL выбирает записи без повтора с `due_date` в диапазоне и записи с повтором, у которых `due_date <= rng.end`.
  2. Фильтр `kind` использует `kind.matches` (тип «задача» включает и напоминания; README, «Поиск»).
  3. Если `status` не задан, отменённые исключаются. Если задан, берётся именно он.
  4. Записи без повтора идут в результат как есть. У записей с повтором `occurrences(...)` разворачивает вхождения в диапазоне, учитывая `exdates`. На каждое вхождение создаётся копия записи с `occurrence`, `due_date`, `starts_at` и `ends_at`, сдвинутыми на разницу дней.
  5. Сортировка по `(due_date, записи без времени после записей со временем, starts_at, id)`.
- `overlapping_meetings(user_id, start, end) -> list[Item]` ([:188](../app/adapters/outbound/sqlite/item_repo.py)). Берёт открытые встречи за день `start.date()` и оставляет те, у которых `starts_at < end` и `effective_end() > start`.
- `due_reminders(user_id, now) -> list[Item]` ([:192](../app/adapters/outbound/sqlite/item_repo.py)). Всё выполняется в одной транзакции под блокировкой.
  1. Выбирает открытые напоминания со временем, у которых `notified_at IS NULL` или есть `rrule`.
  2. Для записи с повтором вызывает `_due_occurrence`. Для записи без повтора пропускает ту, у которой `starts_at > now`.
  3. Для всех отобранных ставит `notified_at = now` и в базе, и в объекте.
  4. Возвращает отобранные записи.
  Порт описывает то же поведение: открытые напоминания с `starts_at <= now` и `notified_at IS NULL`, ставит `notified_at`. Для серий отбор делает `_due_occurrence`.
- `_due_occurrence(i, now) -> Item | None` ([:213](../app/adapters/outbound/sqlite/item_repo.py)), статический метод. Вызывает `last_due(rrule, due_date, время суток в MSK, notified_at или created_at, now, exdates)` (`app/domain/recurrence.py`). Если есть вхождение, возвращает копию записи с `occurrence` и `starts_at`, равным моменту вхождения. Иначе `None`. Начальная точка — последнее оповещение, а при его отсутствии время создания: так не срабатывают вхождения, наступившие до создания записи.

Причина общей схемы повторов (README, «Повторяющиеся записи»): серия хранится одной строкой, вхождения разворачивает репозиторий для списков, проверки конфликтов и поиска напоминаний.

### 4.3. Векторный поиск (`sqlite/vector_index.py`)

Реализует `SearchIndex.search`. Поиск смысловой: запрос и названия записей превращаются в векторы e5, затем сравниваются косинусной близостью (векторы нормированы, поэтому достаточно скалярного произведения).

Константа `_STOP` ([vector_index.py:12](../app/adapters/outbound/sqlite/vector_index.py)). Служебные слова, которые убираются из запроса при втором пороге: «задача/задачу/задачи», «встреча/встречу/встречи», «созвон», «напоминание», «найди», «найти», «про», «по», «с», «со», «у», «меня», «когда», «была», «был», «я», «ставил», «мои», «мой».

Функции:

- `_stems(text) -> set[str]` ([:39](../app/adapters/outbound/sqlite/vector_index.py)). Приводит текст к нижнему регистру, заменяет `ё` на `е`, берёт слова из букв `а-я` и `a-z` длиной от 3 и обрезает каждое до 5 первых букв. Комментарий в коде: префиксная псевдо-основа вместо морфологии, `pymorphy` или `Natasha` — если понадобится точнее.
- `_in_range(i, a, b) -> bool` ([:44](../app/adapters/outbound/sqlite/vector_index.py)). Попадает ли запись в диапазон дат. Без дня — нет. Для серии проверяет `occurrences(...)`. Иначе `a <= день <= b`.
- `_share(a, b) -> bool` ([:53](../app/adapters/outbound/sqlite/vector_index.py)). Есть ли пара основ, где одна начинается с другой («Иван» и «Иваном»).

`SqliteVectorIndex(db, embedder, floor=0.80, semantic_only=0.88)` ([:61](../app/adapters/outbound/sqlite/vector_index.py)). `floor` — нижний порог близости запроса. `semantic_only` — порог близости запроса без служебных слов, при котором запись проходит без общей основы.

`search(user_id, query, filters, limit) -> list[Item]` ([:68](../app/adapters/outbound/sqlite/vector_index.py)):

1. Строит условие SQL: владелец, статус не `cancelled`.
2. Если задан `filters.kind`, добавляет `kind IN (...)` по `kind.matches`.
3. `tense == "past"`: `due_date < today`. `tense == "future"`: `due_date >= today` или есть `rrule`.
4. Считает вектор запроса `embed_query(query)`.
5. Выбирает строки `items` вместе с `items_vec`. Если строк нет, возвращает `[]`.
6. Считает близость всех строк матричным умножением. Комментарий в коде: полный скан хватает примерно до 100 тысяч записей, дальше нужен ANN-индекс.
7. Убирает из запроса слова из `_STOP` и получает `core`. Считает основы `qs = _stems(core)`.
8. Если `core` не пуст, считает вторую близость: вектор `core` с теми же векторами записей. Иначе нули. Комментарий в коде: слова типа записи доминируют в векторе, поэтому порог `semantic_only` применяется к очищенному запросу.
9. Оставляет запись, если близость запроса `>= floor` и либо близость очищенного запроса `>= semantic_only`, либо `_share(qs, _stems(title))`.
10. Сортирует по возрастанию `-близость`, затем по расстоянию в днях между `due_date` записи и `today` (записи без даты уходят в конец).
11. Если заданы `date_from` или `date_to`, оставляет записи в диапазоне через `_in_range`. Если задана одна граница, диапазон состоит из одного дня.
12. Возвращает первые `limit` записей.

Причины (README, «Поиск» и «Решения и причины»):

- Два порога. Бессмысленный запрос получал от e5 близость около 0,83, поэтому одного порога мало. Короткий запрос из одного имени получал низкую близость, и его спасает общая основа.
- Служебные слова убираются для второго порога: они перевешивали смысл («встреча с Иннокентием» находила встречи с Анной).
- Тип записи задаёт модель полем `kind`, а не слова запроса.
- Опечатки не исправляются: слово с опечаткой в первых пяти буквах не даст общей основы, а близость может не дотянуть до 0,88.

### 4.4. Хранилище диалога (`sqlite/dialog_store.py`)

`SqliteDialogStore(db, now=SystemClock().now, pending_ttl=600)` ([dialog_store.py:13](../app/adapters/outbound/sqlite/dialog_store.py)). `now` — функция текущего времени, `pending_ttl` — срок жизни вопроса ассистента в секундах.

`load(session_id) -> DialogState` ([:19](../app/adapters/outbound/sqlite/dialog_store.py)):

1. Читает строку. Если строки нет или `updated_at` старше 24 часов, возвращает пустой `DialogState()`.
2. Разбирает JSON через `DialogState.model_validate_json`. Ошибка валидации (подкласс `ValueError`) логируется предупреждением, возвращается пустое состояние.
3. Если у `pending` есть `created_at` и он старше `pending_ttl`, возвращает копию состояния без `pending`.
4. Иначе возвращает состояние.

`save(session_id, state)` ([:36](../app/adapters/outbound/sqlite/dialog_store.py)):

1. Берёт текущее время.
2. Если у `pending` нет `created_at`, ставит его.
3. Ставит `updated_at` в состояние.
4. Записывает `INSERT OR REPLACE` с JSON состояния и временем.

Причина 10 минут (README, «Диалог»): без срока давно забытый вопрос перехватывал не связанную с ним фразу. Срок задаётся переменной `DIALOG_PENDING_TTL_SECONDS`. Срок 24 часа для всего состояния жёстко задан в коде.

### 4.5. Журнал сообщений (`sqlite/message_log.py`)

`SqliteMessageLog(db, clock=None)` ([message_log.py:16](../app/adapters/outbound/sqlite/message_log.py)). Без `clock` берёт `SystemClock()`.

- `add(user_id, role, text, source, items=None) -> int` ([:21](../app/adapters/outbound/sqlite/message_log.py)). Если переданы записи, сериализует их в JSON через `dataclasses.asdict`, значения с `isoformat` (даты и время) приводит к строке. Вставляет строку и возвращает её `id`.
- `since(user_id, after_id, limit=100) -> list[dict]` ([:29](../app/adapters/outbound/sqlite/message_log.py)). Берёт последние `limit` сообщений новее `after_id` и возвращает их по возрастанию `id`. Каждый элемент — словарь `id`, `role`, `text`, `source`, `items` (список, пустой если записей нет), `created_at`.
- `get(user_id, message_id) -> dict | None` ([:49](../app/adapters/outbound/sqlite/message_log.py)). Одно сообщение владельца, без поля `items`. Без совпадения возвращает `None`.

### 4.6. Пользователи и сессии (`sqlite/user_store.py`)

`SqliteUserStore(db, clock=None)` ([user_store.py:8](../app/adapters/outbound/sqlite/user_store.py)).

- `upsert_user(yandex_uid, login, display_name) -> tuple[User, bool]` ([:13](../app/adapters/outbound/sqlite/user_store.py)). Внутренний `id` — `"u" + yandex_uid`. Если пользователя с таким `yandex_uid` нет, вставляет строку и возвращает `True`. Иначе обновляет `login` и `display_name` и возвращает `False`.
- `save_state(state, expires_at, now)` ([:28](../app/adapters/outbound/sqlite/user_store.py)). Удаляет просроченные `state`, затем сохраняет новый.
- `pop_state(state, now) -> bool` ([:35](../app/adapters/outbound/sqlite/user_store.py)). Читает срок и всегда удаляет запись. Возвращает `True`, если запись была и срок не вышел (`expires_at >= now`). Одноразовость защищает вход от login CSRF (README: значение `state` сверяется и с cookie `oauth_state`).
- `create_session(token_hash, user_id, expires_at, now)` ([:41](../app/adapters/outbound/sqlite/user_store.py)). Удаляет просроченные сессии, затем сохраняет новую.
- `user_by_session(token_hash, now) -> User | None` ([:49](../app/adapters/outbound/sqlite/user_store.py)). Соединяет сессию с пользователем. Если срок вышел, удаляет сессию и возвращает `None`.
- `delete_session(token_hash)` ([:61](../app/adapters/outbound/sqlite/user_store.py)). Удаляет сессию.

### 4.7. Привязки календаря (`sqlite/calendar_links.py`)

`SqliteCalendarLinks(db, key)` ([calendar_links.py:12](../app/adapters/outbound/sqlite/calendar_links.py)). `key` — ключ Fernet в виде байтов.

- `_enc(s) -> str` ([:17](../app/adapters/outbound/sqlite/calendar_links.py)). Шифрует строку Fernet и возвращает текст.
- `save(link)` ([:20](../app/adapters/outbound/sqlite/calendar_links.py)). `INSERT OR REPLACE`: шифрует access-токен, refresh-токен (если есть), записывает `expires_at` в ISO.
- `get(user_id) -> CalendarLink | None` ([:34](../app/adapters/outbound/sqlite/calendar_links.py)). Читает и расшифровывает. Если записи нет, возвращает `None`. Если расшифровка не удалась (`InvalidToken`) или срок не разобрался (`ValueError`), пишет предупреждение `calendar link unreadable` и возвращает `None`: как если бы календарь не был подключён.
- `delete(user_id)` ([:53](../app/adapters/outbound/sqlite/calendar_links.py)). Удаляет запись.

Причина шифрования (README, «Безопасность и вход»): токены хранятся долго и дают доступ к календарю. Ключ `TOKEN_ENCRYPTION_KEY` лежит только в `.env`. Выгрузка выключена без ключа и при `AUTH_DISABLED=1`.

## 5. Эмбеддер e5 (`e5_embedder.py`)

`E5Embedder(session, tokenizer)` ([e5_embedder.py:6](../app/adapters/outbound/e5_embedder.py)). Модель `intfloat/multilingual-e5-small` в формате ONNX, работает локально на CPU. Выбрана, потому что ТЗ требует искать по близким по смыслу запросам, модель бесплатная и многоязычная (README, «Стек»).

- `REPO` — идентификатор модели на Hugging Face.
- `__init__` сохраняет сессию ONNX Runtime и токенизатор, запоминает имена входов модели.
- `load() -> E5Embedder` (классовый метод, [:15](../app/adapters/outbound/e5_embedder.py)). Тяжёлые импорты (`onnxruntime`, `huggingface_hub`, `tokenizers`) делаются внутри функции. Скачивает `onnx/tokenizer.json` и `onnx/model.onnx`, включает дополнение до одной длины и усечение до 128 токенов. Сессия использует `CPUExecutionProvider`. README: модель скачивается при сборке Docker-образа.
- `_emb(texts) -> list[list[float]]` ([:27](../app/adapters/outbound/e5_embedder.py)). Токенизирует пачку, строит `input_ids` и `attention_mask`. Если модель принимает `token_type_ids`, подаёт нули. Берёт скрытые состояния, усредняет по маске (mean pooling) и нормирует вектор до единичной длины.
- `embed_passages(texts)` ([:38](../app/adapters/outbound/e5_embedder.py)). Добавляет префикс `passage: ` к каждому тексту. Пустой список возвращает пустым.
- `embed_query(text)` ([:41](../app/adapters/outbound/e5_embedder.py)). Добавляет префикс `query: ` и возвращает один вектор.

Префиксы `passage:` и `query:` в коде не объяснены в комментариях.

## 6. Распознавание речи (`gigaam_stt.py`)

`RATE = 16000` ([gigaam_stt.py:11](../app/adapters/outbound/gigaam_stt.py)) — частота дискретизации модели.

`GigaAMSpeechToText(model, max_seconds=30)` ([:16](../app/adapters/outbound/gigaam_stt.py)). Модель GigaAM v3 через `onnx_asr`, работает локально на CPU, голос никуда не уходит (README, «Стек»).

- `load(name)` (классовый метод, [:22](../app/adapters/outbound/gigaam_stt.py)). Загружает модель `onnx_asr.load_model(name)`.
- `decode(audio: bytes) -> np.ndarray` ([:25](../app/adapters/outbound/gigaam_stt.py)). Превращает байты в массив `float32` 16 кГц моно.
  1. Если данные начинаются с `RIFF`, пробует прочитать как WAV. Если формат ровно 16 кГц, 1 канал, 16 бит, читает не больше `(max_seconds + 1) * RATE` кадров и нормирует в диапазон от -1 до 1. Ffmpeg не нужен. Комментарий в коде: WAV из голосового цикла уже в нужном формате; README: подготовка заняла 0,1 мс вместо 110 мс.
  2. Если WAV не подошёл или ошибка чтения, запускает `ffmpeg` с `-nostdin`, `-protocol_whitelist pipe`, `-t max_seconds+1`, выход `f32le`, 1 канал, 16 кГц, таймаут 15 с.
  3. Таймаут или `OSError` дают `AssistantError(STT_FAILED)`. Ненулевой код возврата ffmpeg — тоже `STT_FAILED` с текстом `stderr`.
  Причина `-protocol_whitelist pipe` и `-t 31` (README, «Данные»): защита ввода.
- `transcribe(audio) -> str` ([:65](../app/adapters/outbound/gigaam_stt.py)). Декодирует. Если дольше `max_seconds` — `AssistantError(AUDIO_TOO_LONG)`. Если пусто — пустая строка. Иначе распознаёт `model.recognize(wave, sample_rate=RATE)`. Любая ошибка модели превращается в `STT_FAILED`.

Усечение до `max_seconds + 1` секунд при декодировании нужно, чтобы запись длиннее лимита всё равно превышала его на проверке и давала `AUDIO_TOO_LONG`, но не читалась целиком. Код этого не комментирует: вывод следует из сравнения `len(wave) > max_seconds * RATE`.

## 7. Синтез речи (`vosk_tts.py`)

`RATE = 22050` ([vosk_tts.py:9](../app/adapters/outbound/vosk_tts.py)) — частота модели `vosk-model-tts-ru`.

`VoskTTS(synth, speaker)` ([:12](../app/adapters/outbound/vosk_tts.py)). Атрибуты: движок синтеза, номер голоса, `threading.Lock` и `last_text`.

- Блокировка нужна, потому что цикл Джарвиса и `/api/speech` делят один движок (комментарий в коде).
- `last_text` — последний озвученный текст после нормализации.
- `load(model, speaker, cache=~/.cache/vosk)` ([:20](../app/adapters/outbound/vosk_tts.py)). Если в кэше есть папка с именем модели, загружает её локально без сетевого запроса списка моделей (комментарий в коде). Иначе скачивает модель по имени. Импорт `vosk_tts` делается внутри функции: он тяжёлый. README: модель грузится около минуты, поэтому загружается в фоне при старте сервера.
- `synthesize(text) -> bytes` ([:26](../app/adapters/outbound/vosk_tts.py)). Нормализует текст для речи (`app/domain/speech_text.py`). Под блокировкой запоминает `last_text` и синтезирует. Упаковывает отсчёты в WAV (1 канал, 16 бит, 22 050 Гц) и возвращает байты.

Голос выбран прослушиванием нескольких бесплатных вариантов. Голос номер 4 мужской: ассистента зовут Джарвис (README, «Голос»).

## 8. Проигрыватель (`audio_player.py`)

Не реализует порт. Помогает голосовому циклу.

- `TONES` ([audio_player.py:11](../app/adapters/outbound/audio_player.py)). Сигналы: `start` (880 → 1320 Гц), `sent` (660 Гц), `error` (330 Гц). Первая частота — начало, вторая — конец.
- `RATE = 22050`.
- `scale_wav(data, volume) -> bytes` ([:18](../app/adapters/outbound/audio_player.py)). Меняет громкость WAV. При `volume == 1` возвращает данные как есть. Если данные не WAV или не 16-бит, тоже возвращает как есть. Иначе умножает каждый отсчёт на `volume` и ограничивает диапазоном 16-бит. Комментарий в коде: используется нативный порядок байт, на всех целевых платформах он little-endian. README: у сигналов своя громкость, пользователь просил настраивать её отдельно от речи.
- `sound(name, ms=150) -> bytes` ([:40](../app/adapters/outbound/audio_player.py)). Генерирует синусоиду с плавной сменой частоты и затуханием по краям (10 мс). Амплитуда 12 000. Результат кэшируется `functools.cache`.
- `play_wav(data, timeout=30, volume=1.0)` ([:58](../app/adapters/outbound/audio_player.py)). Выбирает `pw-play -`, а если его нет — `aplay -q -`. Передаёт данные через stdin после `scale_wav`. Ошибки процесса и `OSError` логирует предупреждением и не пробрасывает: сбой звука не должен ронять цикл.

## 9. Часы (`system_clock.py`)

`SystemClock.now() -> datetime` ([system_clock.py:7](../app/adapters/outbound/system_clock.py)). Возвращает текущее время в поясе `Europe/Moscow` (`MSK` из `app/domain/dates.py`) с обнулёнными секундами и микросекундами. Часовой пояс один на всех (README, «Ограничения»). Код берёт «сейчас» только из порта `Clock`: так тесты подставляют своё время (README, «Качество»).

## 10. Яндекс ID (`yandex_id.py`)

`YandexIdProvider(client_id, client_secret, redirect_uri, http=None)` ([yandex_id.py:11](../app/adapters/outbound/yandex_id.py)). `http` — клиент `httpx.Client`, по умолчанию новый.

- `authorize_url(state) -> str` ([:18](../app/adapters/outbound/yandex_id.py)). Адрес `https://oauth.yandex.ru/authorize` с параметрами `response_type=code`, `client_id`, `redirect_uri`, `state`.
- `fetch_user(code) -> Profile` ([:22](../app/adapters/outbound/yandex_id.py)).
  1. `POST https://oauth.yandex.ru/token` с `grant_type=authorization_code`, кодом, ключом и секретом приложения, таймаут 10 с.
  2. Берёт `access_token`.
  3. `GET https://login.yandex.ru/info?format=json` с заголовком `Authorization: OAuth <токен>`.
  4. Возвращает `Profile`: `yandex_uid` (строкой), `login`, `display_name` (если пуст — логин), `email` из `default_email`, токены и срок жизни (`expires_in`).
  5. `HTTPStatusError` даёт `AssistantError(AUTH_FAILED)` с текстом `http <код>`. Любая другая ошибка (сеть, не-JSON, нет полей) — `AUTH_FAILED` с именем типа исключения.

Текст исключения в подробности не попадает: комментарий в коде — в нём может быть токен. Цепочка исключений обрывается (`from None`).

## 11. Яндекс Календарь

Выгрузка идёт по CalDAV, в одну сторону: из ассистента в календарь (README, «Яндекс Календарь»). Заголовок `Authorization: OAuth <токен>` соответствует документации Яндекс 360, пароли приложений не нужны.

### 11.1. `yandex_calendar/ics.py`

- `_TZ`, `_VTIMEZONE` ([ics.py:9](../app/adapters/outbound/yandex_calendar/ics.py), [:10](../app/adapters/outbound/yandex_calendar/ics.py)). Параметр `TZID=Europe/Moscow` и блок `VTIMEZONE` с фиксированным смещением `+0300`, имя `MSK`.
- `_esc(s) -> str` ([:23](../app/adapters/outbound/yandex_calendar/ics.py)). Экранирует текст по правилам iCalendar: переводы строк в `\n`, затем `\`, `;`, `,`.
- `_fold(line) -> str` ([:28](../app/adapters/outbound/yandex_calendar/ics.py)). Переносит длинные строки. Первая строка до 75 байт в UTF-8, продолжения до 74 байт плюс ведущий пробел. Символ не разрывается между строками, разрыв идёт по границе символа.
- `_f(d) -> str` ([:39](../app/adapters/outbound/yandex_calendar/ics.py)). Формат `YYYYMMDDTHHMMSS`.
- `build_ics(item, uid, now) -> str` ([:43](../app/adapters/outbound/yandex_calendar/ics.py)).
  1. Если у записи нет `starts_at`, бросает `ValueError`.
  2. Переводит начало в MSK. Конец напоминания — начало плюс 15 минут. Для других типов — `effective_end()` в MSK.
  3. Собирает `VCALENDAR` с блоком `VTIMEZONE` и одним `VEVENT`: `UID`, `DTSTAMP` (UTC, из `now`), `DTSTART`, `DTEND` с `TZID`, `SUMMARY` (экранированное название).
  4. Если есть `rrule`, добавляет `RRULE`.
  5. Если есть `exdates`, добавляет `EXDATE` с временем начала на каждую дату.
  6. Для напоминания добавляет `VALARM` (`ACTION:DISPLAY`, `TRIGGER:PT0S`, описание — название): сигнал в момент начала.
  7. Складывает строки, каждую завершает `\r\n`.

Выбор, какие записи выгружать, делает вызывающий сценарий, а не `build_ics`. README: выгружаются встречи и напоминания со временем, задачи и записи без времени — нет; встреча занимает свою длительность (по умолчанию час).

### 11.2. `yandex_calendar/caldav.py`

`_fail(detail) -> AssistantError` ([caldav.py:18](../app/adapters/outbound/yandex_calendar/caldav.py)). Создаёт `AssistantError(CALENDAR_FAILED, detail)`.

`YandexCalendarSync(links, client_id, client_secret, clock, http=None)` ([:22](../app/adapters/outbound/yandex_calendar/caldav.py)). `links` — хранилище привязок (`CalendarLinks`). Параметры `client_id` и `client_secret` нужны для обновления токена.

- `_link(user_id) -> CalendarLink` ([:32](../app/adapters/outbound/yandex_calendar/caldav.py)). Возвращает действующую привязку.
  1. Читает привязку. Ошибка хранилища даёт `CALENDAR_FAILED` с именем типа исключения. Если привязки нет — `CALENDAR_FAILED` с `no link`.
  2. Если `expires_at > now`, возвращает привязку.
  3. Если токен истёк и нет `refresh_token`, `CALENDAR_FAILED` с `no refresh token`.
  4. Иначе `POST https://oauth.yandex.ru/token` с `grant_type=refresh_token`, таймаут 10 с. Новый `access_token` обязателен. `refresh_token` берётся из ответа, а если его нет, остаётся старый. Срок — `now + expires_in`. Если `expires_in` не пришёл, срок — 365 суток.
  5. Ошибка HTTP даёт `CALENDAR_FAILED` с `http <код>`, остальные ошибки — с именем типа.
  6. Сохраняет обновлённую привязку. Ошибка сохранения — `CALENDAR_FAILED`.
  Текст исключения в подробности не попадает: в нём может быть токен (комментарий в коде).
- `_send(method, item, uid, body=None, ok404=False)` ([:73](../app/adapters/outbound/yandex_calendar/caldav.py)).
  1. Проверяет `uid` по шаблону `[A-Za-z0-9_-]{1,64}`. Иначе `CALENDAR_FAILED` с `bad uid`.
  2. Получает привязку через `_link(item.user_id)`.
  3. Адрес: `https://caldav.yandex.ru/calendars/<email>/events-default/<uid>.ics` (почта кодируется `quote` без безопасных символов).
  4. Заголовок `Authorization: OAuth <токен>`. Если есть тело, добавляется `Content-Type: text/calendar; charset=utf-8`.
  5. Выполняет запрос, таймаут 10 с. Ошибка сети — `CALENDAR_FAILED` с типом исключения.
  6. Любой неуспешный статус — `CALENDAR_FAILED` с `http <код>`. Исключение: статус 404 при `ok404=True` не считается ошибкой.
- `push(item) -> str` ([:94](../app/adapters/outbound/yandex_calendar/caldav.py)). UID — `item.external_id` или новый `uuid4().hex`. Строит ICS на время `clock.now()`. Если у записи нет `starts_at`, `CALENDAR_FAILED` с `no starts_at`. Отправляет `PUT`. Возвращает UID. Повторный `push` с тем же UID перезаписывает событие: так перенос и переименование обновляют то же событие.
- `remove(item)` ([:103](../app/adapters/outbound/yandex_calendar/caldav.py)). Если есть `external_id`, отправляет `DELETE`, и 404 игнорирует. Без `external_id` ничего не делает.

Если календарь недоступен, запись всё равно сохраняется, а к ответу добавляется «Запись сохранена, но в календарь не попала.» (текст `ErrorCode.CALENDAR_FAILED`). Если календарь не подключён, выгрузка пропускается молча: без этого на каждую встречу приходило «в календарь не попала», хотя ошибки не было (README). Эту логику реализует вызывающий сценарий, не адаптер.
