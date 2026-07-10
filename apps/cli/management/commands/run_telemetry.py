"""CLI entry point"""

from __future__ import annotations

import getpass
import sys

from django.core.management.base import BaseCommand

from omnicomm.client import OmnicommClient
from omnicomm.exceptions import OmnicommAPIError, OmnicommAuthError, OmnicommError
from omnicomm.session import set_active_client

BANNER = """
============================================================
       Telemetry Analytics Service
============================================================
"""


class Command(BaseCommand):
    help = "Authenticate with Omnicomm and list available reports and vehicles."

    def handle(self, *args, **options):
        self.stdout.write(BANNER)

        login = self._prompt_login()
        password = self._prompt_password()

        client = OmnicommClient()

        try:
            auth_response = client.login(login=login, password=password)
        except OmnicommAuthError as exc:
            self.stderr.write(self.style.ERROR(f"\nAuthentication failed: {exc}\n"))
            sys.exit(1)
        except OmnicommError as exc:
            self.stderr.write(self.style.ERROR(f"\nUnexpected Omnicomm error: {exc}\n"))
            sys.exit(1)

        set_active_client(client)

        server_name = auth_response.get("server_name", client.server_name)
        self.stdout.write(self.style.SUCCESS("\nAuthentication successful!\n"))

        try:
            reports = client.get_available_reports()
            vehicles = client.flatten_vehicles()
        except OmnicommAPIError as exc:
            self.stderr.write(self.style.ERROR(f"\nFailed to load resources: {exc}\n"))
            sys.exit(1)

        self._print_reports(reports)
        self._print_vehicles(vehicles)

        self.stdout.write(
            self.style.SUCCESS(
                "\nSuccess!\n"
            )
        )

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

    def _print_vehicles(self, vehicles) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("--- Available Vehicles ---"))

        if not vehicles:
            self.stdout.write("  (no vehicles found in the account tree)\n")
            return

        for index, vehicle in enumerate(vehicles, start=1):
            self.stdout.write(
                f"  {index:>3}. {vehicle.name}  (terminal_id: {vehicle.terminal_id})"
            )

        self.stdout.write("")
