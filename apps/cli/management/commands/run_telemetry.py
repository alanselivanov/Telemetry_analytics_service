"""Main interactive entry point for the local telemetry application."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from django.db import connection
from django.core.management.base import BaseCommand

from analytics.services import (
    FuelAnalysisExecution,
    VehicleAnalysisTarget,
    run_mock_fuel_analysis,
    run_multi_vehicle_fuel_analysis,
    run_real_fuel_analysis,
)
from calibration.models import CalibrationTable, Vehicle
from calibration.parser import CalibrationParseError, parse_calibration_file, save_calibration_grid
from cli.time_range import (
    TimeRangeParseError,
    chunk_time_range,
    format_timestamp,
    parse_time_range,
)
from omnicomm.client import OmnicommClient, VehicleInfo
from omnicomm.exceptions import OmnicommAPIError, OmnicommAuthError, OmnicommError
from omnicomm.session import set_active_client, set_session_vehicles

BANNER = """
============================================================
       Telemetry Analytics Service — интерактивный режим
============================================================
"""

ACTION_MENU = """
Выберите аналитическое действие:
  1. Проверить исправность ДУТ (Диагностика)
  2. Найти заправки и сливы за период (Топливный аудит)
  3. Посчитать баланс топлива и расход
  4. [Отладка] Запустить симуляцию аномалий (Mock-анализ без API)
  5. Назад к выбору ТС / Выход
"""


class Command(BaseCommand):
    help = "Главная интерактивная команда: авторизация, выбор ТС и топливная аналитика."

    def handle(self, *args, **options):
        self.stdout.write(BANNER)

        self._warn_if_database_is_not_postgresql()

        login = self._prompt_login()
        password = self._prompt_password()

        client = OmnicommClient()

        try:
            client.login(login=login, password=password)
        except OmnicommAuthError as exc:
            self.stderr.write(self.style.ERROR(f"\nОшибка авторизации: {exc}\n"))
            sys.exit(1)
        except OmnicommError as exc:
            self.stderr.write(self.style.ERROR(f"\nОшибка Omnicomm: {exc}\n"))
            sys.exit(1)

        set_active_client(client)
        self.stdout.write(self.style.SUCCESS("\nАвторизация выполнена успешно.\n"))

        try:
            reports = client.get_available_reports()
            vehicles = client.flatten_vehicles()
        except OmnicommAPIError as exc:
            self.stderr.write(self.style.ERROR(f"\nНе удалось загрузить справочники: {exc}\n"))
            sys.exit(1)

        set_session_vehicles(vehicles)

        self._print_reports(reports)
        self._print_vehicles(vehicles)

        if not vehicles:
            self.stderr.write(
                self.style.ERROR("\nДоступных ТС нет. Работа невозможна.\n")
            )
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("\nСессия сохранена в памяти. Переходим к анализу.\n"))
        self._run_main_loop(client, vehicles)

    def _run_main_loop(self, client: OmnicommClient, vehicles: list[VehicleInfo]) -> None:
        while True:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Выбор ТС и периода ---"))

            api_vehicles = self._select_vehicles(vehicles)
            targets = self._prepare_analysis_targets(api_vehicles)
            if not targets:
                continue
            date_from, date_to, chunks = self._prompt_time_range()

            while True:
                action = self._select_action()
                if action == 5:
                    decision = self._prompt_back_or_exit()
                    if decision == "exit":
                        self.stdout.write(self.style.SUCCESS("\nРабота завершена. До свидания!\n"))
                        return
                    break

                if action == 4:
                    execution = run_mock_fuel_analysis()
                    self._print_full_report(execution, "Mock-анализ без API")
                    continue

                try:
                    executions = self._run_real_analysis(
                        client=client,
                        targets=targets,
                        chunks=chunks,
                    )
                except (OmnicommAPIError, ValueError) as exc:
                    self.stderr.write(self.style.ERROR(f"\nОшибка анализа: {exc}\n"))
                    continue

                for execution in executions:
                    if action == 1:
                        self._print_diagnostics_report(execution)
                    elif action == 2:
                        self._print_fuel_events_report(execution)
                    elif action == 3:
                        self._print_balance_report(execution)

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"\nРезультаты сохранены в БД. ID запуска анализа: "
                            f"{execution.analysis_run.id}\n"
                        )
                    )

    def _select_vehicles(self, vehicles: list[VehicleInfo]) -> list[VehicleInfo]:
        self._print_vehicles(vehicles)

        while True:
            raw = input(
                "Выберите ТС (номер, terminal_id, имя или несколько через запятую, "
                "например: 5,12,33): "
            ).strip()
            if not raw:
                self.stderr.write(self.style.WARNING("Введите номер, ID или имя ТС.\n"))
                continue

            tokens = [token.strip() for token in raw.split(",") if token.strip()]
            selected: list[VehicleInfo] = []
            seen_terminal_ids: set[int] = set()

            for token in tokens:
                match = self._resolve_vehicle_token(token, vehicles)
                if match is None:
                    self.stderr.write(self.style.WARNING(f"ТС не найдено: {token}\n"))
                    selected = []
                    break
                if match.terminal_id not in seen_terminal_ids:
                    selected.append(match)
                    seen_terminal_ids.add(match.terminal_id)
            else:
                if selected:
                    if len(selected) > 1:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Выбрано ТС: {len(selected)} "
                                f"(параллельная обработка включена).\n"
                            )
                        )
                    return selected

            self.stderr.write(self.style.WARNING("Попробуйте ещё раз.\n"))

    def _resolve_vehicle_token(
        self,
        token: str,
        vehicles: list[VehicleInfo],
    ) -> VehicleInfo | None:
        if token.isdigit():
            value = int(token)
            if 1 <= value <= len(vehicles):
                return vehicles[value - 1]
            for vehicle in vehicles:
                if vehicle.terminal_id == value:
                    return vehicle
            return None

        matches = [vehicle for vehicle in vehicles if token.lower() in vehicle.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            self.stderr.write(
                self.style.WARNING(
                    f"Найдено несколько ТС по запросу '{token}'. "
                    "Уточните номер или terminal_id.\n"
                )
            )
            for match in matches[:10]:
                self.stdout.write(f"  {match.name} (terminal_id: {match.terminal_id})")
            return None
        return None

    def _prepare_analysis_targets(
        self,
        api_vehicles: list[VehicleInfo],
    ) -> list[VehicleAnalysisTarget]:
        targets: list[VehicleAnalysisTarget] = []

        for api_vehicle in api_vehicles:
            vehicle = self._sync_vehicle(api_vehicle)
            calibration_table = self._ensure_active_calibration(vehicle)
            if calibration_table is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"ТС пропущено из-за отсутствия тарировки: {vehicle.name}\n"
                    )
                )
                continue
            targets.append(
                VehicleAnalysisTarget(
                    vehicle=vehicle,
                    calibration_table=calibration_table,
                )
            )

        if not targets:
            self.stdout.write("Нет ТС с активной тарировкой для анализа.\n")
        return targets

    def _select_vehicle(self, vehicles: list[VehicleInfo]) -> VehicleInfo:
        return self._select_vehicles(vehicles)[0]

    def _prompt_time_range(self) -> tuple[int, int, list[tuple[int, int]]]:
        while True:
            raw = input(
                "Введите период (например: '1 hour', '2 weeks', '3 months', "
                "'01.07.2026 - 05.07.2026'): "
            ).strip()

            try:
                date_from, date_to = parse_time_range(raw)
                chunks = chunk_time_range(date_from, date_to)
            except (TimeRangeParseError, ValueError):
                self.stderr.write(
                    self.style.WARNING(
                        "Некорректный период. Примеры: '1 hour', '2 weeks', "
                        "'3 months', '01.07.2026 - 05.07.2026'.\n"
                    )
                )
                continue

            self.stdout.write(
                "\n--- Период подготовлен ---\n"
                f"  Начало : {date_from} ({format_timestamp(date_from)})\n"
                f"  Конец  : {date_to} ({format_timestamp(date_to)})\n"
                f"  Чанки  : {len(chunks)} "
                f"({'один запрос' if len(chunks) == 1 else 'разбиение по 7 дней'})\n"
            )
            return date_from, date_to, chunks

    def _select_action(self) -> int:
        self.stdout.write(ACTION_MENU)

        while True:
            raw = input("Введите номер действия (1-5): ").strip()
            try:
                action = int(raw)
            except ValueError:
                self.stderr.write(self.style.WARNING("Введите число от 1 до 5.\n"))
                continue

            if 1 <= action <= 5:
                return action

            self.stderr.write(self.style.WARNING("Действие вне диапазона. Введите 1-5.\n"))

    def _prompt_login(self) -> str:
        while True:
            login = input("Логин Omnicomm: ").strip()
            if login:
                return login
            self.stderr.write(self.style.WARNING("Логин не может быть пустым.\n"))

    def _prompt_password(self) -> str:
        while True:
            password = getpass.getpass("Пароль Omnicomm: ")
            if password:
                return password
            self.stderr.write(self.style.WARNING("Пароль не может быть пустым.\n"))

    def _print_reports(self, reports) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("--- Доступные отчёты ---"))

        if not reports:
            self.stdout.write("  (в JWT не найдены разрешения на отчёты)\n")
            return

        for index, report in enumerate(reports, start=1):
            self.stdout.write(f"  {index:>3}. {report.name}")

        self.stdout.write("")

    def _print_vehicles(self, vehicles: list[VehicleInfo]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("--- Доступные ТС ---"))

        if not vehicles:
            self.stdout.write("  (ТС в дереве аккаунта не найдены)\n")
            return

        for index, vehicle in enumerate(vehicles, start=1):
            self.stdout.write(
                f"  {index:>3}. {vehicle.name}  (terminal_id: {vehicle.terminal_id})"
            )

        self.stdout.write("")

    def _sync_vehicle(self, api_vehicle: VehicleInfo) -> Vehicle:
        vehicle, _ = Vehicle.objects.update_or_create(
            terminal_id=api_vehicle.terminal_id,
            defaults={"name": api_vehicle.name},
        )
        return vehicle

    def _ensure_active_calibration(self, vehicle: Vehicle) -> CalibrationTable | None:
        table = (
            CalibrationTable.objects.filter(vehicle=vehicle, is_active=True)
            .prefetch_related("points")
            .first()
        )
        if table:
            self.stdout.write(
                f"Активная тарировка: {table.name} ({table.sensor_count} датч.)\n"
            )
            return table

        self.stdout.write(
            self.style.WARNING(
                "Для выбранного ТС нет активной тарировочной таблицы.\n"
            )
        )
        while True:
            raw_path = input("Укажите путь к CSV/TXT тарировке или 'о' для отмены: ").strip()
            if raw_path.lower() in {"о", "отмена", "cancel"}:
                self.stdout.write("Загрузка тарировки отменена. Возврат к выбору ТС.\n")
                return None

            path = Path(raw_path.strip('"'))
            if not path.exists():
                self.stderr.write(self.style.WARNING("Файл не найден. Попробуйте ещё раз.\n"))
                continue

            try:
                grid = parse_calibration_file(path)
            except CalibrationParseError as exc:
                self.stderr.write(self.style.WARNING(f"Ошибка тарировки: {exc}\n"))
                continue

            table = save_calibration_grid(
                vehicle=vehicle,
                name=path.stem,
                grid=grid,
                source_filename=path.name,
                activate=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Тарировка загружена: {table.sensor_count} датч., {len(grid.rows)} строк.\n"
                )
            )
            return table

    def _run_real_analysis(
        self,
        *,
        client: OmnicommClient,
        targets: list[VehicleAnalysisTarget],
        chunks: list[tuple[int, int]],
    ) -> list[FuelAnalysisExecution]:
        self.stdout.write(self.style.MIGRATE_HEADING("\n--- Загрузка и анализ данных ---"))
        self.stdout.flush()

        current_vehicle = {"name": targets[0].vehicle.name if len(targets) == 1 else ""}

        def fetch_progress(
            index: int,
            total: int,
            chunk: tuple[int, int],
            row_count: int = 0,
            vehicle_name: str = "",
        ) -> None:
            suffix = f" ({row_count} строк)" if row_count else " (0 строк)"
            label = vehicle_name or current_vehicle["name"]
            if label:
                prefix = f"[API] {label} — чанк {index}/{total}: "
            else:
                prefix = f"[API] Чанк {index}/{total}: "
            self.stdout.write(
                f"{prefix}{format_timestamp(chunk[0])} -> "
                f"{format_timestamp(chunk[1])}{suffix}"
            )
            self.stdout.flush()

        def analyze_progress(stage: str, *values: int, vehicle_name: str = "") -> None:
            label = f"{vehicle_name}: " if vehicle_name else ""
            if stage == "convert":
                index, total, point_count = values
                self.stdout.write(
                    f"[CPU] {label}Интерполяция сегмента {index}/{total}: {point_count} точек"
                )
            elif stage == "merge":
                (point_count,) = values
                self.stdout.write(f"[CPU] {label}Слияние временной шкалы: {point_count} точек")
            elif stage == "events":
                (point_count,) = values
                self.stdout.write(
                    f"[CPU] {label}Поиск заправок/сливов и диагностика: {point_count} точек"
                )
            elif stage == "done":
                point_count, refuels, drains, diagnostics = values
                self.stdout.write(
                    f"[CPU] {label}Готово: {point_count} точек, "
                    f"заправок {refuels}, сливов {drains}, эпизодов ДУТ {diagnostics}"
                )
            self.stdout.flush()

        def vehicle_progress(completed: int, total: int, vehicle_name: str) -> None:
            current_vehicle["name"] = vehicle_name
            self.stdout.write(
                self.style.SUCCESS(
                    f"[Параллельно] Завершено ТС {completed}/{total}: {vehicle_name}"
                )
            )
            self.stdout.flush()

        if len(targets) == 1:
            target = targets[0]
            execution = run_real_fuel_analysis(
                client=client,
                vehicle=target.vehicle,
                calibration_table=target.calibration_table,
                chunks=chunks,
                fetch_progress_callback=fetch_progress,
                analyze_progress_callback=analyze_progress,
            )
            self._print_execution_header(execution, target.vehicle.name)
            if execution.raw_rows_count == 0:
                self._print_empty_data_warning(target.vehicle)
            return [execution]

        executions = run_multi_vehicle_fuel_analysis(
            client=client,
            targets=targets,
            chunks=chunks,
            vehicle_progress_callback=vehicle_progress,
            fetch_progress_callback=fetch_progress,
            analyze_progress_callback=analyze_progress,
        )
        for execution in executions:
            self._print_execution_header(execution, execution.analysis_run.vehicle.name)
            if execution.raw_rows_count == 0:
                self._print_empty_data_warning(execution.analysis_run.vehicle)
        return executions

    def _print_empty_data_warning(self, vehicle: Vehicle) -> None:
        self.stdout.write(
            self.style.WARNING(
                "\nВнимание: API не вернул ни одной строки телеметрии за выбранный период.\n"
                f"  terminal_id       : {vehicle.terminal_id}\n"
                "  Возможные причины : нет ДУТ на ТС, ТС не выходило на связь, "
                "нет прав на отчёт «Журнал», или за период нет записей LLS.\n"
            )
        )

    def _print_execution_header(self, execution: FuelAnalysisExecution, vehicle_name: str) -> None:
        self.stdout.write(
            "\n--- Сводка запуска ---\n"
            f"  ТС              : {vehicle_name}\n"
            f"  ID анализа       : {execution.analysis_run.id}\n"
            f"  Точек телеметрии : {len(execution.result.points)}\n"
            f"  Сырых строк API  : {execution.raw_rows_count}\n"
            f"  Чанков           : {execution.chunks_count}\n"
            f"  Потоков API      : {execution.io_workers}\n"
            f"  Потоков CPU      : {execution.cpu_workers}\n"
        )

    def _print_diagnostics_report(self, execution: FuelAnalysisExecution) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("--- Диагностика ДУТ ---"))
        if execution.raw_rows_count == 0:
            self.stdout.write("Нет данных для диагностики.\n")
            return

        diagnostics = execution.result.diagnostics
        if not diagnostics:
            self.stdout.write(self.style.SUCCESS("Критичных проблем ДУТ не обнаружено.\n"))
            return

        from collections import Counter

        summary = Counter(diagnostic.reason for diagnostic in diagnostics)
        self.stdout.write("Сводка:")
        for reason, count in summary.most_common():
            self.stdout.write(f"  {reason}: {count}")

        display_limit = 15
        self.stdout.write(f"\nЭпизоды (первые {display_limit} из {len(diagnostics)}):")
        for diagnostic in diagnostics[:display_limit]:
            period = ""
            if diagnostic.started_at:
                period = f" ({format_timestamp(diagnostic.started_at)}"
                if diagnostic.ended_at:
                    period += f" -> {format_timestamp(diagnostic.ended_at)}"
                period += ")"
            self.stdout.write(
                f"  Датчик {diagnostic.sensor_index + 1}: "
                f"{diagnostic.status} — {diagnostic.reason}{period}"
            )

        if len(diagnostics) > display_limit:
            self.stdout.write(
                f"  ... и ещё {len(diagnostics) - display_limit} эпизодов "
                f"(полный список сохранён в БД, ID анализа {execution.analysis_run.id})."
            )
        self.stdout.write("")

    def _print_fuel_events_report(self, execution: FuelAnalysisExecution) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("--- Топливный аудит ---"))
        if execution.raw_rows_count == 0:
            self.stdout.write(
                "Нет данных для анализа — события не обнаружены.\n"
            )
            return

        refuels = execution.result.refuels
        drains = execution.result.drains

        self.stdout.write(f"Заправки: {len(refuels)}")
        for event in refuels:
            self.stdout.write(
                f"  +{event.volume_litres:.1f} л: "
                f"{format_timestamp(event.started_at)} -> {format_timestamp(event.ended_at)}"
            )

        self.stdout.write(f"\nСливы: {len(drains)}")
        for event in drains:
            self.stdout.write(
                f"  -{event.volume_litres:.1f} л: "
                f"{format_timestamp(event.started_at)} -> {format_timestamp(event.ended_at)}"
            )
        self.stdout.write("")

    def _print_balance_report(self, execution: FuelAnalysisExecution) -> None:
        balance = execution.balance
        self.stdout.write(
            self.style.MIGRATE_HEADING("--- Баланс топлива и расход ---")
        )
        self.stdout.write(
            f"  Начальный уровень       : {balance.start_litres:.1f} л\n"
            f"  Конечный уровень        : {balance.end_litres:.1f} л\n"
            f"  Изменение уровня        : {balance.delta_litres:.1f} л\n"
            f"  Заправлено              : {balance.refueled_litres:.1f} л\n"
            f"  Слито                   : {balance.drained_litres:.1f} л\n"
            f"  Оценочный расход        : {balance.estimated_consumption_litres:.1f} л\n"
        )

    def _print_full_report(self, execution: FuelAnalysisExecution, title: str) -> None:
        self._print_execution_header(execution, title)
        self._print_fuel_events_report(execution)
        self._print_diagnostics_report(execution)
        self._print_balance_report(execution)
        self.stdout.write(
            self.style.SUCCESS(
                f"Mock-анализ сохранён в БД. ID запуска: {execution.analysis_run.id}\n"
            )
        )

    def _prompt_back_or_exit(self) -> str:
        while True:
            choice = input(
                "Введите 'т' для выбора другого ТС или 'в' для выхода: "
            ).strip().lower()
            if choice in {"т", "тс", "m", "menu"}:
                return "vehicle"
            if choice in {"в", "выход", "q", "quit"}:
                return "exit"
            self.stderr.write(self.style.WARNING("Введите 'т' или 'в'.\n"))

    def _warn_if_database_is_not_postgresql(self) -> None:
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(
                    "Внимание: сейчас подключена не PostgreSQL БД. "
                    "Результаты будут сохранены в текущую Django-БД.\n"
                )
            )
