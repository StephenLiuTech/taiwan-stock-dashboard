"""Application bootstrap and initial local portfolio seeding."""

from decimal import Decimal

from domain import Currency, Holding, HoldingType, Liability, LiabilityType, Market
from repositories.interfaces import HoldingRepository, LiabilityRepository

SEED_HOLDINGS = (
    Holding(
        id="holding-0050",
        symbol="0050",
        name="元大台灣50",
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal("5800"),
        average_cost=Decimal("83.95"),
        holding_type=HoldingType.ETF,
    ),
    Holding(
        id="holding-2027",
        symbol="2027",
        name="大成鋼",
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal("30000"),
        average_cost=Decimal("41.93"),
    ),
    Holding(
        id="holding-2330",
        symbol="2330",
        name="台積電",
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal("150"),
        average_cost=Decimal("1820.50"),
    ),
    Holding(
        id="holding-8299",
        symbol="8299",
        name="群聯",
        market=Market.TPEX,
        currency=Currency.TWD,
        quantity=Decimal("160"),
        average_cost=Decimal("2499.69"),
    ),
    Holding(
        id="holding-3293",
        symbol="3293",
        name="鈊象",
        market=Market.TPEX,
        currency=Currency.TWD,
        quantity=Decimal("2000"),
        average_cost=Decimal("838.56"),
    ),
)

SEED_LIABILITIES = (
    Liability(
        id="liability-margin-financing",
        liability_type=LiabilityType.MARGIN_FINANCING,
        principal=Decimal("543000"),
        currency=Currency.TWD,
    ),
    Liability(
        id="liability-stock-pledge",
        liability_type=LiabilityType.STOCK_PLEDGE,
        principal=Decimal("998000"),
        currency=Currency.TWD,
    ),
)


class BootstrapService:
    """Initialize storage and seed known data only into a fully empty portfolio."""

    def __init__(
        self,
        connection: object,
        holdings: HoldingRepository,
        liabilities: LiabilityRepository,
    ) -> None:
        self.connection = connection
        self.holdings = holdings
        self.liabilities = liabilities

    def initialize(self) -> bool:
        """Initialize schema and return whether seed records were inserted."""
        if self.holdings.list_all() or self.liabilities.list_all():
            return False
        for holding in SEED_HOLDINGS:
            self.holdings.upsert(holding)
        for liability in SEED_LIABILITIES:
            self.liabilities.upsert(liability)
        return True
