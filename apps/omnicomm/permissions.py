
from __future__ import annotations

import re

REPORT_PERMISSION_PREFIXES = ("ase.reports.", "service.")

REPORT_PERMISSION_MAP: dict[str, str] = {
    "ase.reports.log": "Сырые данные (телеметрия)",
    "ase.reports.fueleventsreport": "Заправки и сливы топлива",
    "ase.reports.fuellevels": "Уровни топлива",
    "ase.reports.speed": "Превышения скорости",
    "ase.reports.enginerpm": "Обороты двигателя",
    "ase.reports.track": "Трек (маршрут)",
    "ase.reports.events": "События",
    "ase.reports.groupevents": "События по группам",
    "ase.reports.movementbyperiod": "Движение за период",
    "ase.reports.movementbystandreport": "Движение по стоянкам",
    "ase.reports.movementbtwstandreport": "Перемещения между стоянками",
    "ase.reports.movementdistribution": "Распределение движения",
    "ase.reports.workbytime": "Работа по времени",
    "ase.reports.workedhours": "Отработанные часы",
    "ase.reports.workdistributionbytime": "Распределение работы по времени",
    "ase.reports.loadbytime": "Нагрузка по времени",
    "ase.reports.loaddistribution": "Распределение нагрузки",
    "ase.reports.shifts": "Смены",
    "ase.reports.fuelconsumption": "Расход топлива",
    "ase.reports.voltage": "Напряжение",
    "ase.reports.temperature": "Температура",
    "ase.reports.enginehours": "Моточасы",
    "ase.reports.refrigeratorstate": "Состояние рефрижератора",
    "ase.reports.geozones": "Геозоны",
    "ase.reports.geozonesreport": "Отчёт по геозонам",
    "ase.reports.drivers": "Водители",
    "ase.reports.driversreport": "Отчёт по водителям",
    "ase.reports.delivery": "Доставки",
    "ase.reports.marchroute.routereport": "Маршрутный отчёт",
    "ase.reports.marchroute.currentruns": "Текущие рейсы",
    "ase.reports.location": "Местоположение",
    "ase.reports.location2": "Местоположение (v2)",
    "ase.reports.locationreport": "Отчёт по местоположению",
    "ase.reports.map2": "Карта",
    "ase.reports.summary": "Сводный отчёт",
    "ase.reports.consolidatedreport": "Консолидированный отчёт",
    "ase.reports.groupstat": "Статистика по группам",
    "ase.reports.groupwork2": "Групповая работа",
    "ase.reports.groupratings": "Рейтинги групп",
    "ase.reports.universal": "Универсальный отчёт",
    "ase.reports.managerreport": "Отчёт менеджера",
    "ase.reports.periodicservice": "Периодическое обслуживание",
    "ase.reports.maintenance": "Техобслуживание",
    "ase.reports.mileage": "Пробег",
    "ase.reports.idle": "Простой",
    "ase.reports.drd": "DRD",
    "service.reports": "Сервисные отчёты",
    "service.reports.fuelbalance": "Баланс топлива (сервис)",
    "service.reports.fuelsheet": "Топливная ведомость",
    "service.fuelbalance": "Баланс топлива",
    "service.safedrivingreport": "Безопасное вождение",
    "service.admin": "Администрирование сервиса",
}

_COMPOUND_WORDS: tuple[str, ...] = (
    "consolidated",
    "refrigerator",
    "distribution",
    "movement",
    "universal",
    "periodic",
    "location",
    "delivery",
    "maintenance",
    "engine",
    "events",
    "levels",
    "report",
    "reports",
    "track",
    "speed",
    "shift",
    "shifts",
    "worked",
    "hours",
    "mileage",
    "manager",
    "ratings",
    "geozones",
    "drivers",
    "current",
    "runs",
    "route",
    "router",
    "stand",
    "group",
    "work",
    "load",
    "fuel",
    "level",
    "event",
    "map",
    "log",
    "rpm",
    "voltage",
    "visibility",
    "balance",
    "sheet",
    "safe",
    "driving",
    "service",
    "stat",
    "by",
    "time",
    "period",
    "btw",
    "between",
)


def humanize_permission(permission: str) -> str:
    if permission in REPORT_PERMISSION_MAP:
        return REPORT_PERMISSION_MAP[permission]

    slug = permission
    for prefix in REPORT_PERMISSION_PREFIXES:
        if permission.startswith(prefix):
            slug = permission[len(prefix) :]
            break

    slug = slug.replace(".", " ").replace("_", " ").strip().lower()
    if not slug:
        return permission

    return _split_compound_slug(slug)


def _split_compound_slug(slug: str) -> str:
    if " " in slug:
        return _title_words(slug.split())

    words: list[str] = []
    remaining = slug
    trailing: list[str] = []

    while True:
        match = re.match(r"^(.+?)(reports?)$", remaining)
        if match and match.group(1):
            trailing.insert(0, match.group(2))
            remaining = match.group(1)
            continue
        break

    while remaining:
        matched = False
        for word in sorted(_COMPOUND_WORDS, key=len, reverse=True):
            if remaining.startswith(word):
                words.append(word)
                remaining = remaining[len(word) :]
                matched = True
                break

        if not matched:
            chunk_match = re.match(r"^[a-z0-9]+", remaining)
            if chunk_match:
                words.append(chunk_match.group())
                remaining = remaining[len(chunk_match.group()) :]
            else:
                remaining = remaining[1:]

    words.extend(trailing)
    return _title_words(words)


def _title_words(words: list[str]) -> str:
    titled: list[str] = []
    for word in words:
        if not word:
            continue
        if word.lower() == "rpm":
            titled.append("RPM")
        elif word.lower() == "drd":
            titled.append("DRD")
        elif word.lower() in {"v2", "2"}:
            titled.append(word.upper() if word == "v2" else word)
        else:
            titled.append(word.capitalize())
    return " ".join(titled)