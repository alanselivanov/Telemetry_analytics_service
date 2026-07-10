"""Custom exceptions for the Omnicomm API client."""


class OmnicommError(Exception):
    """Base exception for Omnicomm client errors."""


class OmnicommAuthError(OmnicommError):
    """Raised when authentication fails or credentials are rejected."""


class OmnicommAPIError(OmnicommError):
    """Raised when an authenticated API request fails."""
