"""In-memory runtime session cache for the active Omnicomm client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import OmnicommClient, VehicleInfo

_active_client: OmnicommClient | None = None
_session_vehicles: list[VehicleInfo] | None = None


def set_active_client(client: OmnicommClient) -> None:
    """Store the authenticated client for the current runtime session."""
    global _active_client
    _active_client = client


def get_active_client() -> OmnicommClient:
    """Return the cached client or raise if no session exists."""
    if _active_client is None:
        raise RuntimeError("No active Omnicomm session. Authenticate first.")
    return _active_client


def set_session_vehicles(vehicles: list[VehicleInfo]) -> None:
    """Cache the flattened vehicle list for interactive CLI navigation."""
    global _session_vehicles
    _session_vehicles = vehicles


def get_session_vehicles() -> list[VehicleInfo]:
    """Return cached vehicles or an empty list if none were stored."""
    return list(_session_vehicles or [])


def clear_active_client() -> None:
    """Remove the cached client and vehicles from runtime memory."""
    global _active_client, _session_vehicles
    _active_client = None
    _session_vehicles = None
