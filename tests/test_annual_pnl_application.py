"""Application, persistence, and CLI tests for annual investment P/L."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database.schema import initialize_schema
from domain import (
    AnnualPnlSnapshot,
    Currency,
    DailySnapshot,
    DividendEvent,
    FxRate,
    InvestmentCostEvent,
    InvestmentCostType,
    Market,
    Transaction,
    TransactionType,
)
from pams.application import AnnualPnlUseCase
from pams.application.send_daily_report import (
    DailyEmailHistoryPoint,
    DailyEmailReport,
)
from pams.cli import main
from pams.delivery import DailyEmailReportRenderer
from repositories.provider import create_repositories
from services import AnnualPnlFxUnavailableError


def transaction(identifier: str, side: TransactionType, day: date) -> Transaction:
    return Transaction(
        id=identifier,
        symbol="2330",
        market=Market.TWSE,
        transaction_type=side,
        trade_date=day,
        settlement_date=day,
        quantity=Decimal("10"),
        price=Decimal("100") if side is TransactionType.BUY else Decimal("120"),
        fees=Decimal("2") if side is TransactionType.BUY else Decimal("3"),
        taxes=Decimal("0") if side is TransactionType.BUY else Decimal("4"),
        currency=Currency.TWD,
    )


def database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    return connection


def daily_snapshot(day: date) -> DailySnapshot:
    return DailySnapshot(
        snapshot_date=day,
        total_market_value=Decimal("0"),
        total_cost_basis=Decimal("0"),
        total_unrealized_pnl=Decimal("25"),
        total_liabilities=Decimal("0"),
        net_asset_value=Decimal("0"),
        leverage_ratio=Decimal("0"),
        high_water_mark=Decimal("0"),
        drawdown=Decimal("0"),
    )


def test_use_case_persists_one_immutable_snapshot_per_date(tmp_path: Path) -> None:
    connection = database(tmp_path / "annual.db")
    repositories = create_repositories("sqlite", connection)
    repositories.transactions.add(
        transaction("buy", TransactionType.BUY, date(2026, 1, 1))
    )
    repositories.transactions.add(
        transaction("sell", TransactionType.SELL, date(2026, 2, 1))
    )
    repositories.daily_snapshots.add(daily_snapshot(date(2026, 2, 1)))
    use_case = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    )

    first = use_case.ensure(date(2026, 2, 1))
    second = use_case.ensure(date(2026, 2, 1), unrealized_pnl=Decimal("999"))

    assert first == second
    assert first.realized_pnl_ytd == Decimal("193")
    assert first.valuation_date == date(2026, 2, 1)
    assert first.other_cost_ytd == Decimal("2")
    assert first.total_pnl_ytd == Decimal("216")
    assert len(repositories.annual_pnl_snapshots.list_for_year(2026)) == 1


def test_non_trading_accounting_date_uses_latest_prior_valuation(
    tmp_path: Path,
) -> None:
    connection = database(tmp_path / "prior-valuation.db")
    repositories = create_repositories("sqlite", connection)
    repositories.daily_snapshots.add(daily_snapshot(date(2026, 8, 28)))
    use_case = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    )

    result = use_case.ensure(date(2026, 8, 29))

    assert result.snapshot_date == date(2026, 8, 29)
    assert result.valuation_date == date(2026, 8, 28)
    assert result.unrealized_pnl == Decimal("25")
    assert repositories.daily_snapshots.get_by_date(date(2026, 8, 29)) is None
    persisted = repositories.annual_pnl_snapshots.get_by_date(date(2026, 8, 29))
    assert persisted == result
    connection.close()


def test_valuation_resolver_never_uses_future_and_fails_without_prior(
    tmp_path: Path,
) -> None:
    connection = database(tmp_path / "no-future.db")
    repositories = create_repositories("sqlite", connection)
    repositories.daily_snapshots.add(daily_snapshot(date(2026, 8, 30)))
    use_case = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    )

    with pytest.raises(RuntimeError, match="no portfolio valuation"):
        use_case.ensure(date(2026, 8, 29))
    connection.close()


def test_next_trading_day_uses_new_valuation_without_rewriting_weekend(
    tmp_path: Path,
) -> None:
    connection = database(tmp_path / "next-session.db")
    repositories = create_repositories("sqlite", connection)
    repositories.daily_snapshots.add(daily_snapshot(date(2026, 8, 28)))
    use_case = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    )
    weekend = use_case.ensure(date(2026, 8, 30))
    repositories.daily_snapshots.add(daily_snapshot(date(2026, 8, 31)))
    session = use_case.ensure(date(2026, 8, 31))

    assert weekend.valuation_date == date(2026, 8, 28)
    assert session.valuation_date == date(2026, 8, 31)
    assert use_case.summary(year=2026, as_of=date(2026, 8, 30)) == weekend
    connection.close()


def test_use_case_resolves_weekend_usd_cost_with_prior_persisted_fx(
    tmp_path: Path,
) -> None:
    connection = database(tmp_path / "weekend-fx.db")
    repositories = create_repositories("sqlite", connection)
    repositories.transactions.add(
        Transaction(
            id="mu-buy",
            symbol="MU",
            market=Market.US,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 6, 27),
            settlement_date=date(2026, 6, 29),
            quantity=Decimal("2"),
            price=Decimal("1139.12"),
            fees=Decimal("1.82"),
            taxes=Decimal("0"),
            currency=Currency.USD,
        )
    )
    repositories.fx_rates.upsert(
        FxRate(
            base_currency=Currency.USD,
            quote_currency=Currency.TWD,
            rate_date=date(2026, 6, 26),
            rate=Decimal("32.125"),
            source="fixture",
        )
    )
    use_case = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    )

    result = use_case.ensure(
        date(2026, 6, 27), unrealized_pnl=Decimal("0"), persist=False
    )

    assert result.other_cost_ytd == Decimal("58.46750")


def test_use_case_fails_when_only_future_fx_is_persisted(tmp_path: Path) -> None:
    connection = database(tmp_path / "future-fx.db")
    repositories = create_repositories("sqlite", connection)
    repositories.transactions.add(
        Transaction(
            id="mu-buy",
            symbol="MU",
            market=Market.US,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 6, 27),
            settlement_date=date(2026, 6, 29),
            quantity=Decimal("2"),
            price=Decimal("1139.12"),
            fees=Decimal("1.82"),
            taxes=Decimal("0"),
            currency=Currency.USD,
        )
    )
    repositories.fx_rates.upsert(
        FxRate(
            base_currency=Currency.USD,
            quote_currency=Currency.TWD,
            rate_date=date(2026, 6, 29),
            rate=Decimal("32.250"),
            source="fixture",
        )
    )
    use_case = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    )

    with pytest.raises(
        AnnualPnlFxUnavailableError,
        match="historical USD/TWD FX is unavailable for 2026-06-27",
    ):
        use_case.ensure(date(2026, 6, 27), unrealized_pnl=Decimal("0"), persist=False)


def test_repository_rejects_overwriting_historical_snapshot(tmp_path: Path) -> None:
    connection = database(tmp_path / "immutable.db")
    repository = create_repositories("sqlite", connection).annual_pnl_snapshots
    snapshot = AnnualPnlSnapshot(
        snapshot_date=date(2026, 1, 1),
        valuation_date=date(2026, 1, 1),
        year=2026,
        realized_pnl_ytd=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        dividend_income_ytd=Decimal("0"),
        total_pnl_ytd=Decimal("0"),
    )
    repository.add(snapshot)
    with pytest.raises(sqlite3.IntegrityError):
        repository.add(snapshot)


def test_realized_performance_history_uses_recognition_dates_only(
    tmp_path: Path,
) -> None:
    connection = database(tmp_path / "realized-history.db")
    repositories = create_repositories("sqlite", connection)
    rows = [
        Transaction(
            id="0050-opening",
            symbol="0050",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 1, 1),
            settlement_date=date(2026, 1, 1),
            quantity=Decimal("2150"),
            price=Decimal("1"),
            currency=Currency.TWD,
        ),
        Transaction(
            id="0050-additions",
            symbol="0050",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 7, 1),
            settlement_date=date(2026, 7, 1),
            quantity=Decimal("3650"),
            price=Decimal("1"),
            currency=Currency.TWD,
        ),
        Transaction(
            id="realized-buy",
            symbol="2330",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 1, 2),
            settlement_date=date(2026, 1, 2),
            quantity=Decimal("1"),
            price=Decimal("0"),
            fees=Decimal("5839.154900"),
            currency=Currency.TWD,
        ),
        Transaction(
            id="realized-sell",
            symbol="2330",
            market=Market.TWSE,
            transaction_type=TransactionType.SELL,
            trade_date=date(2026, 8, 1),
            settlement_date=date(2026, 8, 1),
            quantity=Decimal("1"),
            price=Decimal("345393.4500000000000000000013"),
            currency=Currency.TWD,
        ),
    ]
    for row in rows:
        repositories.transactions.add(row)
    repositories.dividend_events.upsert_many(
        [
            DividendEvent(
                source_event_id="existing-dividends",
                symbol="2330",
                market=Market.TWSE,
                name="TSMC",
                dividend_year=2026,
                ex_dividend_date=date(2026, 1, 3),
                payment_date=date(2026, 1, 31),
                cash_dividend_per_share=Decimal("118044.64737350"),
                source="official",
            ),
            DividendEvent(
                source_event_id="0050-january",
                symbol="0050",
                market=Market.TWSE,
                name="Yuanta Taiwan 50",
                dividend_year=2026,
                ex_dividend_date=date(2026, 1, 22),
                payment_date=date(2026, 2, 11),
                cash_dividend_per_share=Decimal("1"),
                source="official",
            ),
            DividendEvent(
                source_event_id="0050-july",
                symbol="0050",
                market=Market.TWSE,
                name="Yuanta Taiwan 50",
                dividend_year=2026,
                ex_dividend_date=date(2026, 7, 21),
                payment_date=date(2026, 8, 10),
                cash_dividend_per_share=Decimal("0.6"),
                source="official",
            ),
        ]
    )
    for event in (
        InvestmentCostEvent(
            id="financing-catchup-margin-through-2026-08-28",
            event_date=date(2026, 8, 28),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("7975.667397260273972602739726"),
            currency=Currency.TWD,
        ),
        InvestmentCostEvent(
            id="financing-catchup-stock-pledge-through-2026-08-28",
            event_date=date(2026, 8, 28),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("30659.876712328767123287671232"),
            currency=Currency.TWD,
        ),
        InvestmentCostEvent(
            id="financing-interest:liability-margin-financing:2026-08-29",
            event_date=date(2026, 8, 29),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("173.4520547945205479452054795"),
            currency=Currency.TWD,
        ),
        InvestmentCostEvent(
            id="financing-interest:liability-stock-pledge:2026-08-29",
            event_date=date(2026, 8, 29),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("177.7260273972602739726027397"),
            currency=Currency.TWD,
        ),
    ):
        repositories.investment_cost_events.add(event)
    use_case = AnnualPnlUseCase(
        repositories.transactions,
        repositories.daily_snapshots,
        repositories.annual_pnl_snapshots,
        repositories.dividend_events,
        repositories.investment_cost_events,
        repositories.fx_rates,
    )

    history = use_case.realized_performance_history(
        year=2026, through=date(2026, 8, 29)
    )
    by_date = {item.snapshot_date: item for item in history}

    assert tuple(by_date) == (
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 31),
        date(2026, 2, 11),
        date(2026, 8, 1),
        date(2026, 8, 10),
        date(2026, 8, 28),
        date(2026, 8, 29),
    )
    assert by_date[date(2026, 1, 1)].realized_total_pnl_ytd == 0
    assert by_date[date(2026, 1, 2)].realized_total_pnl_ytd == Decimal("-5839.154900")
    assert by_date[date(2026, 1, 31)].dividend_income_ytd == Decimal("118044.64737350")
    assert by_date[date(2026, 8, 1)].realized_trading_pnl_ytd == Decimal(
        "345393.4500000000000000000013"
    )
    assert date(2026, 8, 27) not in by_date
    assert by_date[date(2026, 8, 10)].margin_financing_interest_ytd == 0
    assert by_date[date(2026, 8, 10)].stock_pledge_interest_ytd == 0
    assert by_date[date(2026, 8, 28)].margin_financing_interest_ytd == Decimal(
        "7975.667397260273972602739726"
    )
    assert by_date[date(2026, 8, 28)].stock_pledge_interest_ytd == Decimal(
        "30659.87671232876712328767123"
    )
    assert by_date[date(2026, 8, 29)].realized_total_pnl_ytd == Decimal(
        "424242.2202817191780821917821"
    )
    connection.close()


def test_realized_pnl_cli_reads_transaction_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "cli.db"
    connection = database(path)
    repositories = create_repositories("sqlite", connection)
    repositories.transactions.add(
        transaction("buy", TransactionType.BUY, date(2026, 1, 1))
    )
    repositories.transactions.add(
        transaction("sell", TransactionType.SELL, date(2026, 2, 1))
    )
    connection.close()

    result = main(["pnl", "realized", "--year", "2026", "--database", str(path)])

    output = capsys.readouterr().out
    assert result == 0
    assert "Realized P/L Transactions" in output
    assert "2330" in output
    assert "+NT$193" in output


def test_daily_report_portfolio_trend_excludes_annual_pnl_series() -> None:
    report = DailyEmailReport(
        report_date=date(2026, 2, 2),
        verified_source_date=date(2026, 2, 2),
        total_market_value=Decimal("1000"),
        total_cost_basis=Decimal("900"),
        total_unrealized_pnl=Decimal("100"),
        total_return=Decimal("0.1"),
        total_liabilities=Decimal("0"),
        net_asset_value=Decimal("1000"),
        liability_ratio=Decimal("0"),
        daily_profit_loss=Decimal("5"),
        daily_profit_loss_percentage=Decimal("0.005"),
        history=(
            DailyEmailHistoryPoint(
                date(2026, 2, 1),
                Decimal("990"),
                Decimal("990"),
                Decimal("90"),
                Decimal("10"),
                Decimal("80"),
                Decimal("0"),
            ),
            DailyEmailHistoryPoint(
                date(2026, 2, 2),
                Decimal("1000"),
                Decimal("1000"),
                Decimal("110"),
                Decimal("10"),
                Decimal("100"),
                Decimal("0"),
            ),
        ),
        positions=(),
    )

    rendered = DailyEmailReportRenderer().render(report)

    trend_text = rendered.plain_text.split("Portfolio Trend\n", 1)[1].split(
        "\n\nToday's Contributors", 1
    )[0]
    assert "Date | Total stock market value | Net stock equity" in trend_text
    assert "Total P/L YTD" not in trend_text
    assert "Realized P/L YTD" not in trend_text
    assert "Unrealized P/L" not in trend_text
    assert "Dividend Income YTD" not in trend_text
    assert rendered.inline_images[0].content.startswith(b"\x89PNG")
