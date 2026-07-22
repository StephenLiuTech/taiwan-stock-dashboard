"""SQLite transaction boundary for applying transaction-derived holdings."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from repositories.sqlite import SQLiteHoldingRepository, SQLiteTransactionRepository


class SQLiteHoldingRebuildUnitOfWork:
    """Share one explicit transaction across holding and transaction repositories."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.holdings = SQLiteHoldingRepository(connection, auto_commit=False)
        self.transactions = SQLiteTransactionRepository(connection, auto_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
