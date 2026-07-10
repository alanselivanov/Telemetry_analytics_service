
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import OmnicommClient, VehicleInfo

_active_client: OmnicommClient | None = None
_session_vehicles: list[VehicleInfo] | None = None


def set_active_client(client: OmnicommClient) -> None:
    global _active_client
    _active_client = client


def get_active_client() -> OmnicommClient:
    if _active_client is None:
        raise RuntimeError("No active Omnicomm session. Authenticate first.")
    return _active_client


def set_session_vehicles(vehicles: list[VehicleInfo]) -> None:
    global _session_vehicles
    _session_vehicles = vehicles


def get_session_vehicles() -> list[VehicleInfo]:
    return list(_session_vehicles or [])


def clear_active_client() -> None:
    global _active_client, _session_vehicles
    _active_client = None
    _session_vehicles = None