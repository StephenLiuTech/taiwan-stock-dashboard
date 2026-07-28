"""System verification application workflow."""

from pams.application.dto import (
    VerificationItem,
    VerificationLevel,
    VerificationReport,
)
from pams.operations import VerificationService

_MARKET_SOURCE_CHECKS = frozenset(
    {
        "TWSE endpoint",
        "TPEx endpoint",
        "Market Calendar",
    }
)


class VerifySystemUseCase:
    """Execute system checks and expose only application DTOs."""

    def __init__(self, verification_service: VerificationService) -> None:
        self.verification_service = verification_service

    def execute(
        self, *, allow_market_source_warning: bool = False
    ) -> VerificationReport:
        """Return system checks under the requested operational policy.

        The relaxed policy is intended for scheduled delivery. It downgrades
        only temporary official-market reachability failures; local readiness
        and application composition failures remain fatal.
        """
        report = self.verification_service.run()
        return VerificationReport(
            tuple(
                VerificationItem(
                    name=check.name,
                    level=self._level(
                        check.name,
                        VerificationLevel(check.level.value),
                        allow_market_source_warning=allow_market_source_warning,
                    ),
                    detail=check.detail,
                )
                for check in report.checks
            )
        )

    @staticmethod
    def _level(
        check_name: str,
        level: VerificationLevel,
        *,
        allow_market_source_warning: bool,
    ) -> VerificationLevel:
        if (
            allow_market_source_warning
            and level is VerificationLevel.FAIL
            and check_name in _MARKET_SOURCE_CHECKS
        ):
            return VerificationLevel.WARN
        return level
