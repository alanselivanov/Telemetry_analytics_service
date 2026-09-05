# Telemetry Analytics Service

Сервис телеметрии и топливной аналитики на базе Omnicomm. Загружает данные ДУТ, выполняет анализ (диагностика, заправки/сливы, баланс топлива) и отдаёт результаты через REST API.

## Требования

- Docker Desktop (Docker Compose v2)

## Быстрый старт

```bash
cp .env.example .env
# заполнить DATABASE_PASSWORD и учётные данные Omnicomm
docker compose up --build -d
```

После запуска:

| Сервис     | Адрес                    |
|------------|--------------------------|
| REST API   | http://127.0.0.1:8000/api/ |
| PostgreSQL | localhost:5433           |

Миграции БД выполняются автоматически при старте контейнера `app`.

## Конфигурация

Файл `.env` создаётся из `.env.example`. Обязательные параметры:

- `SECRET_KEY` — секрет Django
- `DATABASE_PASSWORD` — пароль PostgreSQL (без него compose не стартует)
- `OMNICOMM_*` — доступ к API Omnicomm (нужен для CLI)

Тарировочные таблицы размещаются в каталоге `calibrations/` — он примонтирован в контейнер как `/app/calibrations` (только чтение).

## CLI

Интерактивный режим анализа:

```bash
docker compose exec app python manage.py run_telemetry
```

Сценарий: авторизация в Omnicomm → выбор ТС → указание периода → действие (диагностика / аудит / баланс). При отсутствии тарировки для ТС запрашивается путь к CSV/TXT-файлу, например:

```
calibrations/тарировка_пример.txt
```

Результаты сохраняются в PostgreSQL и становятся доступны через API.

## REST API

Все эндпоинты — **GET**, тело запроса не используется. Параметры передаются в query string. Авторизация не требуется.

Базовый URL: `http://127.0.0.1:8000/api/`

### Справочники и списки

| Эндпоинт | Описание |
|----------|----------|
| `GET /api/vehicles/` | Список ТС |
| `GET /api/analysis-runs/` | История запусков анализа |
| `GET /api/fuel-events/` | Заправки и сливы |
| `GET /api/sensor-faults/` | Диагностика ДУТ |
| `GET /api/telemetry-points/` | Точки телеметрии |

### Отчёт по ТС

```
GET /api/reports/vehicle/{id}/?from=01.05.2026&to=10.07.2026
```

`{id}` — внутренний ID или `terminal_id` ТС.

Возвращает сводку, баланс топлива, события, диагностику и список прогонов анализа за период.

### Query-параметры

| Параметр | Применение | Формат |
|----------|------------|--------|
| `terminal_id` | Фильтр списков по ТС | число |
| `from`, `to` | Период (обязателен для отчёта) | `DD.MM.YYYY` или unix timestamp |
| `period` | Альтернатива `from`/`to` | `1 hour`, `2 weeks`, `3 months` |
| `include=telemetry` | Добавить точки в отчёт | `telemetry`, `all`, `points` |
| `telemetry_limit` | Лимит точек в отчёте | 1–50000, по умолчанию 5000 |
| `page` | Пагинация списков | номер страницы (100 записей) |

### Примеры

```
GET /api/vehicles/
GET /api/fuel-events/?terminal_id=336048354
GET /api/fuel-events/?terminal_id=336048354&from=11.05.2026&to=10.07.2026
GET /api/reports/vehicle/4/?from=11.05.2026&to=10.07.2026
GET /api/reports/vehicle/4/?period=2+months&include=telemetry
```

Ответ `/api/reports/vehicle/{id}/` без `from`/`to` или `period` — **400 Bad Request**.

## Пересборка после изменений кода

```bash
docker compose up --build -d
```

## Структура проекта

```
apps/
  omnicomm/    — клиент API Omnicomm
  calibration/ — тарировочные таблицы
  analytics/   — движок анализа ДУТ
  reports/     — сохранение результатов
  api/         — REST API
  cli/         — run_telemetry
calibrations/  — файлы тарировки (volume)
```
