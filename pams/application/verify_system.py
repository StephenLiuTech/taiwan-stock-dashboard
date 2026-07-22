"""System verification application workflow."""

from pams.application.dto import (
    VerificationItem,
    VerificationLevel,
    VerificationReport,
)
from pams.operations import VerificationService


class VerifySystemUseCase:
    """Execute system checks and expose only application DTOs."""

    def __init__(self, verification_service: VerificationService) -> None:
        self.verification_service = verification_service

    def execute(self) -> VerificationReport:
        """Return all system checks without formatting them."""
        report = self.verification_service.run()
        return VerificationReport(
            tuple(
                VerificationItem(
                    name=check.name,
                    level=VerificationLevel(check.level.value),
                    detail=check.detail,
                )
                for check in report.checks
            )
        )
