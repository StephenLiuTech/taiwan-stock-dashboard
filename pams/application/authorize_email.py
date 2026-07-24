"""First-time delegated email authorization workflow."""

from collections.abc import Callable
from typing import Protocol


class DeviceAuthorizationProvider(Protocol):
    def authorize(self, show_prompt: Callable[[str, str], None]) -> None: ...


class AuthorizeMicrosoftEmailUseCase:
    """Coordinate an interactive device-code authorization."""

    def __init__(self, provider: DeviceAuthorizationProvider) -> None:
        self._provider = provider

    def execute(self, show_prompt: Callable[[str, str], None]) -> None:
        """Authorize and persist the delegated token cache."""
        self._provider.authorize(show_prompt)
