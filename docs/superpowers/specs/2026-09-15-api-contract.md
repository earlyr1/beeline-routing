# API-контракт: backend <-> frontend

Дата: 2026-09-15. Общий документ для Плана 2 (backend API), Плана 3 (frontend) и Плана 4 (LLM-предложения).
Доменные модели определены в `backend/app/domain/models.py` (План 1) и здесь повторены в JSON-виде.

## Общие правила

- Префикс `/api`. Frontend (nginx) проксирует `/api` в backend. CORS не нужен.
- Время всегда строка `HH:MM`. Часы не ограничены сверху: в плане диспетчеров встречаются значения вроде `25:53` и `49:13`, клиент не должен на них ломаться. Длительности и время в пути в минутах (int), расстояния в км (float, 2 знака), координаты `lat`/`lon` (float).
- Идентификаторы строковые.
- Ошибка: HTTP 4xx/5xx с телом `{"detail": "текст на русском для пользователя"}`. 400 битый файл или запрос, 404 неизвестный id, 409 датасет ещё не готов, 422 невалидное событие, 503 внешний сервис недоступен.
- Состояние хранится в памяти процесса backend. Перезапуск backend теряет загруженные датасеты: это допущение прототипа.

## Перечисления

- `Skill`: `local` (Локальные работы), `connection` (Работы на подключение и дозаказы), `emergency` (Аварийные работы)
- `Transport`: `car` (Автомобиль), `bike` (Велосипед), `public` (Общественный транспорт и пешком). С 16.09.2026 пешеход входит в `public`: на входе прежнее значение `foot` принимается и читается как `public`, в ответах и схеме его нет.
- `Priority`: `normal` (Обычная), `urgent` (Срочная)
- `RequestStatus`: `active`, `cancelled`
- `EventType`: `urgent`, `cancel`, `restore`, `engineer_unavailable`, `engineer_transport_changed`, `request_updated`, `engineer_delayed`
- `ReasonCode`: `no_skill`, `no_transport`, `does_not_fit_window_or_shift`, `no_free_engineer_in_window`, `address_not_found`
- `GeocodePrecision`: `house`, `street`, `locality`, `none`
- `DatasetStatusValue`: `processing`, `ready`, `failed`
- `ProposalStatus`: `pending`, `approved`, `rejected`, `failed`

## Доменные объекты (как в Плане 1)

```jsonc
// Request
{"id": "74198", "address": "Город Москва, пр-кт.Волгоградский, д. 128 к 5", "lat": 55.70, "lon": 37.78,
 "geocode_precision": "house", "district": "Кузьминки", "duration_min": 60,
 "window_start": "20:00", "window_end": "22:00", "priority": "normal", "skill": "connection",
 "transport_required": null, "status": "active", "source_type_bk": "Подключение",
 "source_type_hd": "Конвергенция абонента"}

// Engineer. start_lat/start_lon у каждого свои (медоид истории бригады), это не адрес офиса
{"id": "E01", "name": "Бригада Арташкин", "start_lat": 55.71, "start_lon": 37.80,
 "shift_start": "09:00", "shift_end": "18:00", "skills": ["local", "connection"], "transport": "car",
 "available": true, "unavailable_from": null}

// Office
{"region": "east", "title": "Восток", "address": "г. Москва, ул Юных Ленинцев, д 83с 4", "lat": 55.71, "lon": 37.80}

// Event (валидация: urgent -> request обязателен; cancel/restore -> request_id; engineer_unavailable -> engineer_id)
{"type": "engineer_unavailable", "time": "13:00", "request": null, "request_id": null, "engineer_id": "E03"}

// Visit
{"request_id": "74198", "arrival": "19:40", "start": "20:00", "end": "21:00", "leg_km": 3.4, "leg_min": 12,
 "late_min": 0, "pinned": false}

// Route
{"engineer_id": "E01", "visits": [Visit], "total_km": 23.5, "total_travel_min": 71}

// Unassigned
{"request_id": "50104", "reason_code": "no_skill", "reason_text": "Нет инженера с навыком «Аварийные работы»."}

// Metrics
{"engineers_used": 9, "km_per_engineer": {"E01": 23.5}, "total_km": 180.2, "assigned": 64, "unassigned": 2,
 "violations": 0}

// Plan. solver: "ortools" | "fcfs" | "dispatchers". routes есть у каждого инженера, пустые тоже.
{"solver": "ortools", "routes": [Route], "unassigned": [Unassigned], "metrics": Metrics, "violations": ["..."]}
```

## Новые объекты API

```jsonc
// UploadReport
{"region": "east", "region_title": "Восток", "source": "beeline_csv",   // "beeline_csv" | "bundle"
 "requests": 66, "engineers": 12, "skipped_rows": ["строка 68: нет номера заявки или временного окна"],
 "geocoding": {"house": 50, "street": 12, "locality": 3, "none": 1},
 "not_found": [{"request_id": "86160", "address": "..."}], "matrix_source": "osrm"}  // "osrm" | "haversine"

// DatasetStatus
{"dataset_id": "d_3f9a", "status": "processing", "stage": "geocoding",  // parsing|geocoding|matrix|solving|ready
 "progress": {"done": 12, "total": 66}, "report": null, "error": null}

// AppliedEvent
{"id": "ev_1", "event": Event, "version": 2}   // version плана после применения события

// ConstraintCheck
{"name": "Навык", "ok": true, "detail": "Нужен «Работы на подключение и дозаказы», у инженера есть"}

// Alternative
{"engineer_id": "E04", "feasible": true, "extra_km": 4.2, "start": "10:35",
 "reason": "Может взять, но пробег больше на 4.2 км"}

// Explanation
{"request_id": "74198", "status": "assigned",             // "assigned" | "unassigned" | "cancelled"
 "engineer_id": "E01", "summary": "Назначена Бригада Арташкин: ...",
 "factors": ["Меньше задействованных инженеров", "Кратчайшая вставка в маршрут"],
 "constraints": [ConstraintCheck],                        // Навык, Транспорт, Временное окно, Смена
 "visit": Visit, "alternatives": [Alternative], "unassigned": null}

// DiffMove / DiffAssign / DiffRemove / DiffShift
{"request_id": "1", "from_engineer_id": "E01", "to_engineer_id": "E02"}
{"request_id": "URG-001", "engineer_id": "E05"}
{"request_id": "2", "engineer_id": "E03", "reason": "Заявка отменена"}
{"request_id": "3", "engineer_id": "E02", "old_start": "14:00", "new_start": "14:25", "delta_min": 25}

// PlanDiff
{"moved": [DiffMove], "added": [DiffAssign], "removed": [DiffRemove],
 "reordered_engineers": ["E02"], "time_shifts": [DiffShift],
 "metrics_before": Metrics, "metrics_after": Metrics}

// PlanningState
{"dataset_id": "d_3f9a", "version": 1, "region": "east", "office": Office, "now": "00:00",
 "requests": [Request],            // текущие: со срочными и статусами отмены
 "engineers": [Engineer],          // текущая доступность
 "plan": Plan,                     // оптимизированный текущий
 "previous_plan": Plan | null,     // до последнего события
 "baseline": Plan,                 // FCFS по ТЗ на текущей задаче (после тех же событий)
 "control": Plan | null,           // диспетчеры, если есть контрольное распределение
 "last_diff": PlanDiff | null,
 "events": [AppliedEvent], "matrix_source": "osrm"}

// RouteGeometry. source "osrm" для car/bike (дорожный граф), "straight" для public или без OSRM
{"engineer_id": "E01", "transport": "car", "source": "osrm",
 "legs": [{"to_request_id": "74198", "coordinates": [[37.78, 55.70], [37.79, 55.71]]}]}

// Proposal (План 4)
{"id": "pr_1", "status": "pending", "event": Event, "rationale": "В сообщении сказано, что ...",
 "source_text": "Арташкин заболел после обеда", "created_at_version": 3, "result_diff": null, "error": null}

// ClientConfig
{"yandex_maps_api_key": "…" | null, "llm_enabled": true, "osrm_available": true}
```

## Эндпоинты

| Метод и путь | Тело запроса | Ответ | Примечания |
|---|---|---|---|
| `GET /api/config` | | `ClientConfig` | ключ Яндекс Карт отдаётся с backend из env |
| `GET /api/health` | | `{"status": "ok"}` | для healthcheck compose |
| `POST /api/upload` | multipart `file` (.csv или .json) | `DatasetStatus` (202) | предподсчёт идёт в фоне |
| `GET /api/datasets/{id}` | | `DatasetStatus` | фронт опрашивает раз в секунду |
| `POST /api/datasets/{id}/plan` | | `PlanningState` | считает FCFS и OR-Tools заново, сбрасывает события. 409 пока processing |
| `GET /api/datasets/{id}/state` | | `PlanningState` | 409 если план ещё не построен |
| `POST /api/datasets/{id}/events` | `Event` | `PlanningState` | `last_diff` заполнен; время события не раньше `now` текущего состояния |
| `POST /api/datasets/{id}/timeline/events` | `Event` | `PlanningState` | событие на шкалу времени; курсор не двигается |
| `DELETE /api/datasets/{id}/timeline/events/{entry_id}` | | `PlanningState` | убрать событие со шкалы |
| `POST /api/datasets/{id}/cursor` | `{"time": "HH:MM"}` | `PlanningState` | текущее время дня |
| `GET /api/datasets/{id}/explain/{request_id}` | | `Explanation` | по текущему плану |
| `GET /api/datasets/{id}/routes/{engineer_id}/geometry` | query `plan=current\|previous` | `RouteGeometry` | кэшируется |
| `GET /api/geocode/reverse` | query `lat`, `lon` | `{"address": str\|null, "precision": GeocodePrecision}` | адрес по точке для «Добавить заявку здесь» |
| `POST /api/datasets/{id}/chat` | `{"text": "..."}` | `{"proposals": [Proposal], "clarification": str\|null}` | План 4; 503 если LLM не настроен |
| `GET /api/datasets/{id}/proposals` | | `[Proposal]` | План 4 |
| `POST /api/datasets/{id}/proposals/{proposal_id}/approve` | | `{"proposal": Proposal, "state": PlanningState}` | План 4 |
| `POST /api/datasets/{id}/proposals/{proposal_id}/reject` | | `Proposal` | План 4 |
| `POST /api/datasets/{id}/proposals/approve-all` | | `{"proposals": [Proposal], "state": PlanningState}` | применяет pending по порядку создания |
| `POST /api/datasets/{id}/proposals/reject-all` | | `[Proposal]` | План 4 |

## Раскладка frontend (для Планов 3 и 4)

- `frontend/src/api/types.ts` все типы выше, `frontend/src/api/client.ts` функции по одной на эндпоинт.
- `frontend/src/store/useAppStore.ts` zustand: `datasetId`, `datasetStatus`, `state: PlanningState | null`, `selectedRequestId`, `selectedEngineerId`, `activeTab`, `showPrevious: boolean`, действия `upload`, `plan`, `applyEvent`, `selectRequest`, `selectEngineer`, `setTab`.
- Вкладки правой панели регистрируются в `frontend/src/components/panel/tabs.ts` массивом `{id, title, component}`; План 4 добавляет вкладку `proposals` одной строкой.
- Цвет инженера: `frontend/src/lib/colors.ts`, функция `engineerColor(engineerId, engineerIds)`.

## Принятые уточнения (после Планов 2 и 3)

Эти пункты имеют приоритет над текстом выше.

1. `PlanningState.baseline` это FCFS, пересчитанный на текущей задаче после тех же событий и с теми же закреплёнными визитами.
2. `POST /api/datasets/{id}/plan` без событий возвращает план предподсчёта. Если события были, день пересобирается: `version` = прежняя + 1, `events` = `[]`, `now` = `"00:00"`, `previous_plan` = `null`.
3. `DatasetStatus.stage` при `failed` остаётся на этапе ошибки. `progress` считает только геокодируемые адреса, иначе `{"done": 0, "total": 0}`.
4. `PlanDiff.time_shifts` включает и заявки, перенесённые к другому инженеру. `removed[].reason`: «Заявка отменена», текст причины неназначения или «Снята с плана».
5. `RouteGeometry`: первый участок начинается в стартовой точке инженера; участок на каждый визит, включая закреплённые. `source: "osrm"` только если все участки по дорогам; `public` всегда `"straight"`. `plan=previous` при `previous_plan = null` отвечает 404.
6. `Explanation.alternatives`: сначала допустимые по возрастанию `extra_km`, затем недопустимые. У закреплённого визита альтернатив нет. `summary` начинается с «Исполнитель <имя>.».
7. Срочная заявка может прийти без `lat`/`lon`: backend геокодирует адрес. Если адрес не найден, заявка попадает в неназначенные с причиной `address_not_found`.
8. Отмена визита, который уже начался к `now`, отклоняется с 422 и текстом «Заявка … уже в работе с HH:MM, отменить её нельзя.». Возврат заявки, окно которой закончилось раньше `now`, тоже 422.
9. Загрузка файла «Контрольное распределение» завершается статусом `failed` с подсказкой загрузить «Синтетические данные».
10. Swagger доступен по `/api/docs`, схема по `/api/openapi.json`.
11. В `frontend/src/api/types.ts` контрактный `Request` называется `ServiceRequest`, `Event` называется `PlanEvent`, чтобы не затенять DOM-типы. Поля совпадают.
12. `Visit.pinned = true` только у работы, начатой до `now`: её нельзя отменить. Визит, к которому инженер уже едет, солвер не переназначает, но у него `pinned = false`, и отмена до начала работы разрешена. Объяснение такого визита: «Исполнитель <имя> уже в пути к заявке, работа начнётся в HH:MM.», альтернатив нет.
13. Время события по умолчанию во фронте 13:00, но не раньше `now`. Окно срочной заявки по умолчанию начинается не раньше самого раннего начала смены доступных инженеров.
14. Событие `engineer_transport_changed` («Смена транспорта»): клиент передаёт `engineer_id`, новый `transport` и `time`. У всех событий есть необязательные поля `transport` и `previous_transport` (по умолчанию `null`); `previous_transport` заполняет backend в сохранённом событии. Ошибки 422: «Инженер <id> не найден.», «<имя> недоступен с HH:MM, сменить транспорт нельзя.», «У <имя> уже транспорт «<название>».». Начатые визиты и визит в пути сохраняются, дальше маршрут считается по новому транспорту. LLM-помощник предлагает событие инструментом `propose_engineer_transport_change`.
15. Событие `request_updated` («Изменение заявки»): клиент передаёт `request_id` и полный `request` с тем же `id`. Изменяются `address`, `lat`, `lon`, `duration_min`, `window_start`, `window_end`, `priority`, `skill`, `transport_required`; `id`, `status`, `district` и исходные типы берутся из сохранённой заявки. Если `lat`/`lon` пустые, а адрес изменился, backend геокодирует его; пустые координаты при прежнем адресе оставляют старые. У всех событий есть необязательное поле `previous_request`, backend заполняет его в сохранённом событии. Ошибки 422: «Заявка <id> не найдена.», «Заявка <id> уже в работе с HH:MM, изменить её нельзя.», «Окно заявки <id> заканчивается в HH:MM, это раньше времени события HH:MM.», «Конец окна заявки <id> должен быть позже начала.», «Адрес «<адрес>» не найден на карте. Укажите точку на карте.», «В заявке <id> ничего не изменилось.». Удержание визита в пути для изменённой заявки снимается. LLM-помощник предлагает событие инструментом `propose_request_update`. Одобренное предложение хранит применённое событие с `previous_request` и `previous_transport`.
16. Известное расхождение: если в `request_updated` конец окна строго раньше начала, запрос отклоняет общая валидация модели с текстом «Некорректный запрос: request: конец временного окна раньше начала». Текст «Конец окна заявки <id> должен быть позже начала.» приходит, когда конец совпадает с началом. Фронт проверяет окно до отправки.
17. Событие `engineer_delayed` («Задержка инженера»): клиент передаёт `engineer_id`, `delay_min` (целое, от 5 до 480) и `time`. У всех событий есть необязательное поле `delay_min`. Ошибки 422: «Инженер <id> не найден.», «<имя> недоступен с HH:MM, задержку поставить нельзя.», «задержка должна быть от 5 до 480 минут». У `PlanDiff` появляется необязательное поле `delay_forecast` (только для этого события): `engineer_id`, `delay_min`, `late_without_replan` (список `request_id`, `planned_start`, `forecast_start`, `late_min`) и `overtime_without_replan_min`. Ранняя задержка продолжает действовать при следующих событиях. Две задержки подряд, пока инженер едет к заявке, не складываются. LLM-помощник предлагает событие инструментом `propose_engineer_delay`.
19. Срочная заявка «как можно скорее»: у `Request` есть поле `asap` (по умолчанию `false`). Для события `urgent` с `asap: true` backend ставит окно от времени события до самого позднего конца смены доступных инженеров, присланное окно не используется. В `request_updated` включение `asap` задаёт окно так же, заявка, которая остаётся ASAP, сохраняет окно, выключение берёт присланное окно. Солвер: мягкая граница начала `window_start + 240` мин с весом 200 за минуту, жёсткое окно и штраф за неназначенную срочную заявку прежние. Объяснение показывает проверку «Как можно скорее». Инструмент помощника `propose_urgent_request` принимает `asap` и не требует окна.
18. «Адрес по точке»: `GET /api/geocode/reverse?lat=&lon=` возвращает короткий адрес в формате, который backend снова разбирает, например «Москва, Перовская улица, 42к1», и точность: house при номере дома, street при одной улице, locality при одном населённом пункте, none и `address: null`, если ничего не известно. Точка вне Москвы и области даёт 422 «Точка вне Москвы и Московской области.». Без сети или с `GEOCODER=cache-only` ответ `address: null`. Повторные клики по той же точке берутся из кэша в памяти.
20. Нагрузка инженеров: `POST /api/datasets/{id}/plan` принимает необязательное тело `{"workload_level": 0..2, "lunch": true|false}`, без тела уровень сессии сохраняется. День пересобирается, если были события или уровень отличается от текущего, `version` при пересборке растёт на 1. Ошибка 422 «Некорректный запрос: уровень нагрузки должен быть от 0 до 2». `PlanningState.workload_level` показывает уровень, события перепланируют с ним же. Уровни 0..2 задают стоимость нового инженера 20 000, 1 000 000 и 6 000 000; уровень 1 по умолчанию. FCFS от уровня не зависит.
21. Обед по плану: у маршрута (`Route`) появилось поле `lunch`: `{"start": "HH:MM", "end": "HH:MM"}` или `null`. Обед 45 минут, начало от `shift_start + 180` до `shift_start + 360`, только при рабочем дне от 6 часов и только у инженера с визитами. В плане диспетчеров (`control`) обед всегда `null`. Объяснение назначенной заявки добавляет фактор «Обед HH:MM–HH:MM.», причина «не помещается» может добавлять «с учётом обеда».
22. Обед по выбору и лимиты поиска: `POST /api/datasets/{id}/plan` принимает необязательное поле `lunch` (по умолчанию в сессии `true`), `PlanningState.lunch_enabled` показывает выбор. Без обеда маршруты не содержат `lunch`, симуляция и солвер работают без перерывов. День пересобирается, если были события или изменился уровень или обед. План дня с обедом ищется `SOLVER_TIME_LIMIT_LUNCH_S` (30 с), без обеда `SOLVER_TIME_LIMIT_S` (5 с), пересчёт по событиям всегда `SOLVER_TIME_LIMIT_S`.
23. Часы дня и шкала событий: события хранятся на шкале записями `{id, event}` и применяются в порядке (время события, порядок добавления). План при курсоре T равен исходному плану дня плюс все события со временем не позже T; backend держит снимки сессии после каждого события, поэтому движение курсора без пересечения событий солвер не запускает. `PlanningState` получает `cursor`, `timeline` (`{id, event, status: applied | pending | rejected, reason}`) и `timeline_ready`. `POST /timeline/events` геокодирует адрес при добавлении, проверяет существование инженера и заявки и возвращает 422, если событие со временем не позже курсора отклонено; событие впереди курсора остаётся `pending`. Отклонённое при переигрывании событие пропускается со статусом `rejected` и русской причиной. `POST /cursor` принимает время от 00:00 до 23:59. Старый `POST /events` и одобрение предложений добавляют событие и двигают курсор на его время, тексты прежние. `POST /plan` очищает шкалу и ставит курсор на 00:00. После изменения шкалы снимки досчитываются в фоне, `timeline_ready` показывает готовность.
24. Оборудование и отмены дня: у `Request` есть поле `needs_equipment` (по умолчанию `false`), оно показывается диспетчеру и ни на что не влияет в расчёте. У `Bundle` есть `cancellations` — список `{request_id, time}`, отмены дня из контрольного файла. При построении и пересборке дня шкала времени засевается отменами: обычные записи шкалы, до курсора применённые, впереди ожидающие, их можно удалить, а следующая пересборка сеет их заново. Старые бандлы без обоих полей грузятся со значениями по умолчанию.
