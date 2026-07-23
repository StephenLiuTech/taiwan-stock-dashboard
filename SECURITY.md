# Security Policy

## Supported versions

PAMS provides security fixes for the latest released minor version.

| Version | Supported |
|---|---|
| 0.8.x | Yes |
| Earlier versions | No |
| Unreleased development branches | Best effort only |

This table will be updated when a newer stable version is released.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Use the repository's private GitHub security-advisory reporting channel. Include:

- affected version or commit
- vulnerability description and impact
- reproduction steps or proof of concept
- affected files or components
- suggested mitigation, if known
- whether the issue has been disclosed elsewhere

Do not include real credentials, personal financial data, production databases,
or other sensitive user information. Use synthetic examples.

If private vulnerability reporting is unavailable, contact a repository
maintainer privately and ask for a secure reporting channel before sharing
technical details.

## Expected response process

Maintainers should:

1. Acknowledge receipt within five business days.
2. Triage severity, reproducibility, and affected versions.
3. Establish a private remediation plan and coordinate with the reporter.
4. Add regression coverage without exposing exploit details prematurely.
5. Prepare and validate a fix using the standard quality gates.
6. Publish a security advisory and supported release when remediation is ready.
7. Credit the reporter when requested and appropriate.

Response and release timing depends on severity and complexity. Maintainers will
provide status updates through the private reporting channel and avoid public
disclosure until users have a reasonable opportunity to update.
