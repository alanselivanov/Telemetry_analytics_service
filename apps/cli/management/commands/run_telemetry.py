"""Interactive CLI entry point for Omnicomm authentication and telemetry workflows."""

from __future__ import annotations

import getpass
import sys

from django.core.management.base import BaseCommand

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
       Telemetry Analytics Service
============================================================
"""

ACTION_MENU = """
--- Select Action ---
    1. Build "Raw Data Log (Telemetry)" (POST /ls/api/v1/click/log)
    2. Analyze Fuel Consumption & Drains (Phase 3 — coming soon)
    3. Run Equipment Diagnostics (Phase 4 — coming soon)
"""


class Command(BaseCommand):
    help = "Authenticate with Omnicomm, explore resources, and run telemetry workflows."

    def handle(self, *args, **options):
        self.stdout.write(BANNER)

        login = self._prompt_login()
        password = self._prompt_password()

        client = OmnicommClient()

        try:
            client.login(login=login, password=password)
        except OmnicommAuthError as exc:
            self.stderr.write(self.style.ERROR(f"\nAuthentication failed: {exc}\n"))
            sys.exit(1)
        except OmnicommError as exc:
            self.stderr.write(self.style.ERROR(f"\nUnexpected Omnicomm error: {exc}\n"))
            sys.exit(1)

        set_active_client(client)
        self.stdout.write(self.style.SUCCESS("\nAuthentication successful!\n"))

        try:
            reports = client.get_available_reports()
            vehicles = client.flatten_vehicles()
        except OmnicommAPIError as exc:
            self.stderr.write(self.style.ERROR(f"\nFailed to load resources: {exc}\n"))
            sys.exit(1)

        set_session_vehicles(vehicles)

        self._print_reports(reports)
        self._print_vehicles(vehicles)

        if not vehicles:
            self.stderr.write(
                self.style.ERROR("\nNo vehicles available. Cannot start interactive menu.\n")
            )
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("\nSession cached in memory.\n"))
        self._run_main_loop(vehicles)

    def _run_main_loop(self, vehicles: list[VehicleInfo]) -> None:
        while True:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Main Menu ---"))

            vehicle = self._select_vehicle(vehicles)
            date_from, date_to, chunks = self._prompt_time_range()
            action = self._select_action()

            if action == 1:
                self._execute_raw_data_log(vehicle, chunks)
            elif action == 2:
                self._execute_phase_placeholder(
                    "Analyze Fuel Consumption & Drains",
                    "Phase 3",
                )
            else:
                self._execute_phase_placeholder(
                    "Run Equipment Diagnostics",
                    "Phase 4",
                )

            self.stdout.write(
                f"\n--- Summary ---\n"
                f"  Vehicle    : {vehicle.name} (terminal_id: {vehicle.terminal_id})\n"
                f"  Period     : {format_timestamp(date_from)} -> {format_timestamp(date_to)}\n"
                f"  Chunks     : {len(chunks)}\n"
            )

            if self._prompt_navigation() == "q":
                self.stdout.write(self.style.SUCCESS("\nGoodbye! Telemetry session closed.\n"))
                break

    def _select_vehicle(self, vehicles: list[VehicleInfo]) -> VehicleInfo:
        self._print_vehicles(vehicles)

        while True:
            raw = input(f"Select a vehicle by its number (1-{len(vehicles)}): ").strip()
            try:
                index = int(raw)
            except ValueError:
                self.stderr.write(
                    self.style.WARNING("Invalid input. Enter a number from the list.\n")
                )
                continue

            if 1 <= index <= len(vehicles):
                return vehicles[index - 1]

            self.stderr.write(
                self.style.WARNING(
                    f"Number out of range. Choose a value between 1 and {len(vehicles)}.\n"
                )
            )

    def _prompt_time_range(self) -> tuple[int, int, list[tuple[int, int]]]:
        while True:
            raw = input(
                "Enter time range (e.g., '1 hour', '2 weeks', '3 months', "
                "'01.07.2026 - 05.07.2026'): "
            ).strip()

            try:
                date_from, date_to = parse_time_range(raw)
                chunks = chunk_time_range(date_from, date_to)
            except (TimeRangeParseError, ValueError) as exc:
                self.stderr.write(self.style.WARNING(f"Invalid time range: {exc}\n"))
                continue

            self.stdout.write(
                "\n--- Parsed Time Range ---\n"
                f"  dateFrom : {date_from} ({format_timestamp(date_from)})\n"
                f"  dateTo   : {date_to} ({format_timestamp(date_to)})\n"
                f"  Chunks   : {len(chunks)} "
                f"({'single request' if len(chunks) == 1 else '7-day slices'})\n"
            )
            return date_from, date_to, chunks

    def _select_action(self) -> int:
        self.stdout.write(ACTION_MENU)

        while True:
            raw = input("Select an action (1-3): ").strip()
            try:
                action = int(raw)
            except ValueError:
                self.stderr.write(self.style.WARNING("Invalid input. Enter 1, 2, or 3.\n"))
                continue

            if 1 <= action <= 3:
                return action

            self.stderr.write(self.style.WARNING("Choice out of range. Enter 1, 2, or 3.\n"))

    def _execute_raw_data_log(
        self,
        vehicle: VehicleInfo,
        chunks: list[tuple[int, int]],
    ) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("\n--- Executing Raw Data Log ---"))

        for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
            self.stdout.write(
                f"[API] Chunk {index}/{len(chunks)}: "
                f"from {format_timestamp(chunk_start)} "
                f"to {format_timestamp(chunk_end)} "
                f"for terminal {vehicle.terminal_id}..."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "\n[Success] Data chunks successfully prepared for processing.\n"
            )
        )

    def _execute_phase_placeholder(self, title: str, phase: str) -> None:
        self.stdout.write(
            self.style.WARNING(
                f"\n[{phase}] '{title}' is not implemented yet. Placeholder only.\n"
            )
        )

    def _prompt_navigation(self) -> str:
        while True:
            choice = input("Type 'm' to return to the main menu or 'q' to quit: ").strip().lower()
            if choice in {"m", "q"}:
                return choice
            self.stderr.write(self.style.WARNING("Invalid input. Type 'm' or 'q'.\n"))

    def _prompt_login(self) -> str:
        while True:
            login = input("login: ").strip()
            if login:
                return login
            self.stderr.write(self.style.WARNING("Login cannot be empty. Try again.\n"))

    def _prompt_password(self) -> str:
        while True:
            password = getpass.getpass("password: ")
            if password:
                return password
            self.stderr.write(self.style.WARNING("Password cannot be empty. Try again.\n"))

    def _print_reports(self, reports) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("--- Available Reports ---"))

        if not reports:
            self.stdout.write("  (no report permissions found in JWT token)\n")
            return

        for index, report in enumerate(reports, start=1):
            self.stdout.write(f"  {index:>3}. {report.name}")

        self.stdout.write("")

    def _print_vehicles(self, vehicles: list[VehicleInfo]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("--- Available Vehicles ---"))

        if not vehicles:
            self.stdout.write("  (no vehicles found in the account tree)\n")
            return

        for index, vehicle in enumerate(vehicles, start=1):
            self.stdout.write(
                f"  {index:>3}. {vehicle.name}  (terminal_id: {vehicle.terminal_id})"
            )

        self.stdout.write("")
