"""Deployment contract tests for the production daily-report workflow."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily-report.yml"


def _workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_daily_report_workflow_exists_with_expected_schedule() -> None:
    workflow = _workflow()
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert triggers["schedule"] == [
        {"cron": "35 13 * * 1-5", "timezone": "Asia/Taipei"}
    ]


def test_manual_dispatch_exposes_non_forced_boolean_default() -> None:
    workflow = _workflow()
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    dispatch = triggers["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    inputs = dispatch["inputs"]
    assert isinstance(inputs, dict)
    force_rebuild = inputs["force_rebuild"]
    assert isinstance(force_rebuild, dict)
    assert force_rebuild["type"] == "boolean"
    assert force_rebuild["default"] == "false"


def test_workflow_defines_exact_production_environment_at_job_level() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["send-daily-report"]
    assert isinstance(job, dict)
    assert job["env"] == {
        "PAMS_ENVIRONMENT": "production",
        "PAMS_LOG_LEVEL": "INFO",
        "PAMS_EMAIL_TRANSPORT": "resend",
        "PAMS_US_MARKET_DATA_PROVIDER": "alphavantage",
        "PAMS_FX_PROVIDER": "alphavantage",
        "PAMS_DATABASE_URL": "${{ secrets.PAMS_DATABASE_URL }}",
        "PAMS_RESEND_API_KEY": "${{ secrets.PAMS_RESEND_API_KEY }}",
        "PAMS_EMAIL_FROM": "${{ secrets.PAMS_EMAIL_FROM }}",
        "PAMS_EMAIL_TO": "${{ secrets.PAMS_EMAIL_TO }}",
        "PAMS_SUPABASE_URL": "${{ secrets.PAMS_SUPABASE_URL }}",
        "PAMS_SUPABASE_SERVICE_ROLE_KEY": (
            "${{ secrets.PAMS_SUPABASE_SERVICE_ROLE_KEY }}"
        ),
        "PAMS_REPORT_ASSET_BUCKET": "${{ secrets.PAMS_REPORT_ASSET_BUCKET }}",
        "PAMS_REPORT_ASSET_PREFIX": "${{ secrets.PAMS_REPORT_ASSET_PREFIX }}",
        "PAMS_ALPHA_VANTAGE_API_KEY": "${{ secrets.PAMS_ALPHA_VANTAGE_API_KEY }}",
    }
    steps = job["steps"]
    assert isinstance(steps, list)
    assert all("env" not in step for step in steps if isinstance(step, dict))


def test_scheduled_command_is_non_forced_and_manual_force_is_explicit() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["send-daily-report"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    commands = {
        step["name"]: (step.get("if"), step.get("run"))
        for step in steps
        if isinstance(step, dict) and "run" in step
    }
    assert commands["Verify production configuration and dependencies"] == (
        None,
        "python -m pams verify --allow-market-source-warning",
    )
    assert commands["Send scheduled daily report"] == (
        "${{ github.event_name == 'schedule' }}",
        "python -m pams daily-report send --debug",
    )
    assert commands["Send manual daily report"] == (
        "${{ github.event_name == 'workflow_dispatch' && !inputs.force_rebuild }}",
        "python -m pams daily-report send --debug",
    )
    assert commands["Force rebuild and send manual daily report"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.force_rebuild }}",
        "python -m pams daily-report send --force --debug",
    )


def test_workflow_has_minimum_permissions_concurrency_and_timeout() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "pams-daily-report",
        "cancel-in-progress": "false",
    }
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["send-daily-report"]
    assert isinstance(job, dict)
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "15"
    steps = job["steps"]
    assert isinstance(steps, list)
    assert all(
        "continue-on-error" not in step for step in steps if isinstance(step, dict)
    )
    setup = next(
        step
        for step in steps
        if isinstance(step, dict) and step.get("uses") == "actions/setup-python@v5"
    )
    assert setup["with"] == {"python-version": "3.11", "cache": "pip"}
    install = next(
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Install PAMS runtime dependencies"
    )
    assert install["run"] == "python -m pip install ."
