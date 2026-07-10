
from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import requests

from .exceptions import OmnicommAPIError, OmnicommAuthError
from .permissions import REPORT_PERMISSION_MAP, REPORT_PERMISSION_PREFIXES, humanize_permission

CLICK_LOG_DEFAULT_COLUMNS = ["EVENT_DATE", "SPEED", "LLS_CODE"]
CLICK_LOG_DATA_GROUPS = (
    "GENERAL",
    "NAVI",
    "UNIVAL",
    "CAN",
    "OBD",
    "MODBUS",
    "LLS",
    "IQFREEZE",
)


def default_groups_for_columns(columns: Sequence[str]) -> list[str]:
    groups: list[str] = ["GENERAL"]
    if any(column.startswith("LLS") for column in columns):
        groups.append("LLS")
    return groups


def extract_click_log_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    columns = response.get("columns")
    if not isinstance(columns, list):
        return []
    return [row for row in columns if isinstance(row, dict)]


def _load_omnicomm_config() -> dict[str, Any]:
    from django.conf import settings

    base_url = settings.OMNICOMM_BASE_URL.rstrip("/")
    click_log_path = settings.OMNICOMM_CLICK_LOG_PATH
    if not click_log_path.startswith("/"):
        click_log_path = f"/{click_log_path}"
    vehicle_tree_path = settings.OMNICOMM_VEHICLE_TREE_PATH
    if not vehicle_tree_path.startswith("/"):
        vehicle_tree_path = f"/{vehicle_tree_path}"
    return {
        "login_endpoint": settings.OMNICOMM_LOGIN_URL,
        "vehicle_tree_endpoint": f"{base_url}{vehicle_tree_path}",
        "click_log_endpoint": f"{base_url}{click_log_path}",
        "timeout": settings.OMNICOMM_REQUEST_TIMEOUT,
    }


@dataclass(frozen=True)
class AvailableReport:

    permission: str
    name: str


@dataclass(frozen=True)
class VehicleInfo:

    name: str
    terminal_id: int


class OmnicommClient:

    def __init__(self, timeout: int | None = None) -> None:
        config = _load_omnicomm_config()

        self.jwt: str | None = None
        self.refresh: str | None = None
        self.server_name: str | None = None
        self._login_endpoint: str = config["login_endpoint"]
        self._vehicle_tree_endpoint: str = config["vehicle_tree_endpoint"]
        self._click_log_endpoint: str = config["click_log_endpoint"]
        self._timeout = timeout if timeout is not None else config["timeout"]
        self._http = requests.Session()
        self._thread_local = threading.local()

    def _get_thread_http(self) -> requests.Session:
        session = getattr(self._thread_local, "http", None)
        if session is None:
            session = requests.Session()
            self._thread_local.http = session
        return session

    @property
    def is_authenticated(self) -> bool:
        return bool(self.jwt and self.server_name)

    def login(self, login: str, password: str) -> dict[str, Any]:
        try:
            response = self._http.post(
                self._login_endpoint,
                json={"login": login, "password": password},
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise OmnicommAuthError(f"Unable to reach Omnicomm login endpoint: {exc}") from exc

        if response.status_code != 200:
            detail = self._extract_error_detail(response)
            raise OmnicommAuthError(
                f"Authentication failed ({response.status_code}): {detail}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OmnicommAuthError("Authentication response is not valid JSON.") from exc

        jwt = payload.get("jwt")
        if not jwt:
            raise OmnicommAuthError("Authentication response is missing required field: jwt.")

        self.jwt = jwt
        self.refresh = payload.get("refresh")

        try:
            jwt_payload = self._decode_token_payload(jwt)
        except OmnicommAPIError as exc:
            raise OmnicommAuthError(str(exc)) from exc

        server_name = jwt_payload.get("server_name")
        if not server_name:
            server = jwt_payload.get("server")
            if isinstance(server, dict):
                server_name = server.get("slug")

        if not server_name:
            raise OmnicommAuthError(
                "JWT payload is missing server_name (expected at root or in server.slug)."
            )

        self.server_name = str(server_name)
        return payload

    def _build_headers(self) -> dict[str, str]:
        if not self.is_authenticated:
            raise OmnicommAPIError("Client is not authenticated. Call login() first.")

        return {
            "Authorization": f"JWT {self.jwt}",
            "X-Server-Name": self.server_name,
            "Accept": "application/json",
        }

    def get_vehicle_tree(self) -> list[dict[str, Any]]:

        try:
            response = self._http.get(
                self._vehicle_tree_endpoint,
                headers=self._build_headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise OmnicommAPIError(f"Vehicle tree request failed: {exc}") from exc

        if response.status_code != 200:
            detail = self._extract_error_detail(response)
            raise OmnicommAPIError(
                f"Vehicle tree request failed ({response.status_code}): {detail}"
            )

        try:
            raw_tree = response.json()
        except ValueError as exc:
            raise OmnicommAPIError("Vehicle tree response is not valid JSON.") from exc

        return self._normalize_tree_response(raw_tree)

    def fetch_click_log(
        self,
        *,
        terminal_id: int,
        date_from: int,
        date_to: int,
        columns: list[str] | None = None,
        groups: list[Any] | None = None,
    ) -> dict[str, Any]:
        resolved_columns = list(columns or CLICK_LOG_DEFAULT_COLUMNS)
        resolved_groups = (
            list(groups)
            if groups is not None
            else default_groups_for_columns(resolved_columns)
        )
        payload = {
            "terminalId": int(terminal_id),
            "dateFrom": int(date_from),
            "dateTo": int(date_to),
            "groups": resolved_groups,
            "columns": resolved_columns,
        }

        try:
            response = self._get_thread_http().post(
                self._click_log_endpoint,
                json=payload,
                headers=self._build_headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise OmnicommAPIError(f"Click log request failed: {exc}") from exc

        if response.status_code != 200:
            detail = self._extract_error_detail(response)
            raise OmnicommAPIError(
                f"Click log request failed ({response.status_code}): {detail}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise OmnicommAPIError("Click log response is not valid JSON.") from exc

        if not isinstance(data, dict):
            raise OmnicommAPIError("Click log response must be a JSON object.")
        if not isinstance(data.get("columns"), list):
            raise OmnicommAPIError("Click log response is missing 'columns' array.")

        return data

    @staticmethod
    def _normalize_tree_response(raw_tree: Any) -> list[dict[str, Any]]:
        
        if raw_tree is None:
            return []

        if isinstance(raw_tree, list):
            return [node for node in raw_tree if isinstance(node, dict)]

        if isinstance(raw_tree, dict):
            for key in ("tree", "data", "result"):
                nested = raw_tree.get(key)
                if isinstance(nested, list):
                    return [node for node in nested if isinstance(node, dict)]

            if any(key in raw_tree for key in ("children", "objects", "name", "id")):
                return [raw_tree]

        raise OmnicommAPIError(
            "Vehicle tree response has an unexpected format "
            f"(expected TreeGroup-V2 object or array, got {type(raw_tree).__name__})."
        )

    def decode_jwt_payload(self) -> dict[str, Any]:
        if not self.jwt:
            raise OmnicommAPIError("JWT token is not available. Authenticate first.")

        return self._decode_token_payload(self.jwt)

    @staticmethod
    def _decode_token_payload(token: str) -> dict[str, Any]:
        parts = token.split(".")
        if len(parts) < 2:
            raise OmnicommAPIError("JWT token format is invalid.")

        payload_segment = parts[1]
        padding = "=" * (-len(payload_segment) % 4)

        try:
            decoded = base64.urlsafe_b64decode(payload_segment + padding)
            payload = json.loads(decoded)
        except (ValueError, json.JSONDecodeError) as exc:
            raise OmnicommAPIError("Failed to decode JWT payload.") from exc

        if not isinstance(payload, dict):
            raise OmnicommAPIError("JWT payload must be a JSON object.")

        return payload

    def get_available_reports(self) -> list[AvailableReport]:
        payload = self.decode_jwt_payload()
        permissions = payload.get("permissions", [])

        if not isinstance(permissions, list):
            raise OmnicommAPIError("JWT payload field 'permissions' must be an array.")

        reports: list[AvailableReport] = []
        seen: set[str] = set()

        for permission in permissions:
            if not isinstance(permission, str):
                continue
            if not permission.startswith(REPORT_PERMISSION_PREFIXES):
                continue
            if permission in seen:
                continue

            seen.add(permission)
            name = REPORT_PERMISSION_MAP.get(permission, humanize_permission(permission))
            reports.append(AvailableReport(permission=permission, name=name))

        reports.sort(key=lambda item: item.name.lower())
        return reports

    def flatten_vehicles(self, tree: list[dict[str, Any]] | None = None) -> list[VehicleInfo]:
        if tree is None:
            tree = self.get_vehicle_tree()

        vehicles_by_id: dict[int, VehicleInfo] = {}
        stack: list[dict[str, Any]] = list(tree)

        while stack:
            node = stack.pop()

            for obj in node.get("objects") or []:
                if not isinstance(obj, dict):
                    continue

                name = obj.get("name")
                terminal_id = obj.get("terminal_id")

                if name is None or terminal_id is None:
                    continue

                tid = int(terminal_id)
                if tid not in vehicles_by_id:
                    vehicles_by_id[tid] = VehicleInfo(name=str(name), terminal_id=tid)

            for child in node.get("children") or []:
                if isinstance(child, dict):
                    stack.append(child)

        vehicles = list(vehicles_by_id.values())
        vehicles.sort(key=lambda item: item.name.lower())
        return vehicles

    @staticmethod
    def _extract_error_detail(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            text = response.text.strip()
            return text[:200] if text else response.reason

        if isinstance(body, dict):
            for key in ("message", "detail", "error", "errors"):
                value = body.get(key)
                if value:
                    return str(value)

        return str(body)[:200]