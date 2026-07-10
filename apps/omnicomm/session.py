"""In-memory runtime session cache for the active Omnicomm client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import OmnicommClient

_active_client: OmnicommClient | None = None


def set_active_client(client: OmnicommClient) -> None:
    """Store the authenticated client for the current runtime session."""
    global _active_client
    _active_client = client


def get_active_client() -> OmnicommClient:
    """Return the cached client or raise if no session exists."""
    if _active_client is None:
        raise RuntimeError("No active Omnicomm session. Authenticate first.")
    return _active_client


def clear_active_client() -> None:
    """Remove the cached client from runtime memory."""
    global _active_client
    _active_client = None
