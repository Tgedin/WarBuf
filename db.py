"""
SQLite persistence layer.

One Database instance per process. Schema is created on first open.
All writes are explicit — no ORM magic.
WAL mode for safe concurrent reads (e.g. a monitoring script alongside the bot).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker            TEXT    NOT NULL,
        date              TEXT    NOT NULL,
        action            TEXT    NOT NULL,
        score             REAL,
        rules_hash        TEXT    NOT NULL,
        vetoed            INTEGER NOT NULL DEFAULT 0,
        veto_reason       TEXT,
        bull_case         TEXT,
        bear_case         TEXT,
        data_gaps         TEXT,   -- JSON array stored as text
        model_confidence  TEXT,
        confidence_reason TEXT,
        self_critique     TEXT,
        data_request      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT    NOT NULL,
        side            TEXT    NOT NULL,
        qty             REAL    NOT NULL,
        price_usd       REAL    NOT NULL,
        price_eur       REAL,
        fees_usd        REAL    NOT NULL,
        net_cost_basis  REAL    NOT NULL,
        ibkr_order_id   TEXT,
        date            TEXT    NOT NULL,
        eur_usd_rate    REAL    DEFAULT 1.0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        ticker              TEXT    PRIMARY KEY,
        qty                 REAL    NOT NULL DEFAULT 0,
        avg_cost_basis      REAL    NOT NULL DEFAULT 0,
        avg_cost_basis_eur  REAL    NOT NULL DEFAULT 0,
        total_fees_paid     REAL    NOT NULL DEFAULT 0,
        total_fees_eur      REAL    NOT NULL DEFAULT 0,
        first_buy_date      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS performance (
        date                TEXT    PRIMARY KEY,
        portfolio_value_eur REAL,
        benchmark_value     REAL,
        cash_eur            REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forecasts (
        month            TEXT    PRIMARY KEY,
        expected_low     REAL,
        expected_high    REAL,
        actual           REAL,
        benchmark_actual REAL,
        notes            TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_cash (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        balance_eur REAL    NOT NULL DEFAULT 0.0,
        updated     TEXT    NOT NULL
    )
    """,
]


class Database:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._apply_schema()

    def _apply_schema(self) -> None:
        for stmt in _SCHEMA:
            self._conn.execute(stmt)
        # Ensure the cash singleton row always exists (idempotent)
        self._conn.execute(
            "INSERT OR IGNORE INTO portfolio_cash (id, balance_eur, updated) VALUES (1, 0.0, 'init')"
        )
        self._conn.commit()
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        """Add new columns to existing databases. Safe to run on fresh installs."""
        migrations = [
            "ALTER TABLE trades    ADD COLUMN price_eur    REAL",
            "ALTER TABLE trades    ADD COLUMN eur_usd_rate REAL DEFAULT 1.0",
            "ALTER TABLE positions ADD COLUMN avg_cost_basis_eur REAL NOT NULL DEFAULT 0",
            "ALTER TABLE positions ADD COLUMN total_fees_eur     REAL NOT NULL DEFAULT 0",
            "ALTER TABLE decisions ADD COLUMN algorithm_feedback TEXT",
            "ALTER TABLE decisions ADD COLUMN data_request TEXT",
        ]
        for stmt in migrations:
            try:
                self._conn.execute(stmt)
            except Exception:
                pass  # Column already exists
        self._conn.commit()

    # ── Decisions ─────────────────────────────────────────────────────────────

    def record_decision(
        self,
        ticker: str,
        action: str,
        score: float | None,
        rules_hash: str,
        vetoed: bool = False,
        veto_reason: str | None = None,
        bull_case: str | None = None,
        bear_case: str | None = None,
        data_gaps: list[str] | None = None,
        model_confidence: str | None = None,
        confidence_reason: str | None = None,
        self_critique: str | None = None,
        algorithm_feedback: str | None = None,
        data_request: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO decisions
               (ticker, date, action, score, rules_hash,
                vetoed, veto_reason, bull_case, bear_case,
                data_gaps, model_confidence, confidence_reason, self_critique,
                algorithm_feedback, data_request)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker, _today(), action, score, rules_hash,
                int(vetoed), veto_reason, bull_case, bear_case,
                json.dumps(data_gaps or []),
                model_confidence, confidence_reason, self_critique,
                algorithm_feedback, data_request,
            ),
        )
        self._conn.commit()

    def get_recent_decisions(self, ticker: str, limit: int = 3) -> list[dict]:
        """Return the N most recent decision rows for a ticker (newest first)."""
        rows = self._conn.execute(
            """SELECT date, action, score, bull_case, bear_case,
                      self_critique, data_gaps, model_confidence,
                      algorithm_feedback, data_request
               FROM decisions
               WHERE ticker = ?
               ORDER BY date DESC
               LIMIT ?""",
            (ticker, limit),
        ).fetchall()
        result: list[dict] = []
        for r in rows:
            row_dict = dict(r)
            row_dict["data_gaps"] = json.loads(row_dict.get("data_gaps") or "[]")
            result.append(row_dict)
        return result

    # ── Trades ────────────────────────────────────────────────────────────────

    def record_trade(
        self,
        ticker: str,
        side: str,
        qty: float,
        price_usd: float,
        fees_usd: float,
        net_cost_basis: float,
        ibkr_order_id: str | None = None,
        eur_usd_rate: float = 1.0,
    ) -> None:
        price_eur = price_usd / eur_usd_rate
        self._conn.execute(
            """INSERT INTO trades
               (ticker, side, qty, price_usd, price_eur, fees_usd,
                net_cost_basis, ibkr_order_id, date, eur_usd_rate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, side, qty, price_usd, price_eur, fees_usd,
             net_cost_basis, ibkr_order_id, _today(), eur_usd_rate),
        )
        # Cash accounting: buy debits net cost, sell credits net proceeds
        net_cost_basis_eur = net_cost_basis / eur_usd_rate
        delta_eur = -net_cost_basis_eur if side == "buy" else +net_cost_basis_eur
        self.adjust_cash(delta_eur)
        self._update_position(ticker, side, qty, price_usd, fees_usd, eur_usd_rate)
        self._conn.commit()

    def _update_position(
        self,
        ticker: str,
        side: str,
        qty: float,
        price_usd: float,
        fees_usd: float,
        eur_usd_rate: float = 1.0,
    ) -> None:
        price_eur = price_usd / eur_usd_rate
        fees_eur  = fees_usd  / eur_usd_rate
        row = self._conn.execute(
            """SELECT qty, avg_cost_basis, avg_cost_basis_eur,
                      total_fees_paid, total_fees_eur
               FROM positions WHERE ticker = ?""",
            (ticker,),
        ).fetchone()

        if side == "buy":
            if row is None:
                self._conn.execute(
                    """INSERT INTO positions
                       (ticker, qty, avg_cost_basis, avg_cost_basis_eur,
                        total_fees_paid, total_fees_eur, first_buy_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, qty, price_usd, price_eur,
                     fees_usd, fees_eur, _today()),
                )
            else:
                new_qty     = row["qty"] + qty
                new_avg_usd = (row["avg_cost_basis"]     * row["qty"] + price_usd * qty) / new_qty
                new_avg_eur = (row["avg_cost_basis_eur"] * row["qty"] + price_eur * qty) / new_qty
                self._conn.execute(
                    """UPDATE positions
                       SET qty=?, avg_cost_basis=?, avg_cost_basis_eur=?,
                           total_fees_paid=?, total_fees_eur=?
                       WHERE ticker=?""",
                    (new_qty, new_avg_usd, new_avg_eur,
                     row["total_fees_paid"] + fees_usd,
                     row["total_fees_eur"]  + fees_eur, ticker),
                )

        elif side == "sell":
            if row is None:
                return
            new_qty = max(row["qty"] - qty, 0.0)
            if new_qty == 0:
                self._conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
            else:
                self._conn.execute(
                    """UPDATE positions
                       SET qty=?, total_fees_paid=?, total_fees_eur=?
                       WHERE ticker=?""",
                    (new_qty,
                     row["total_fees_paid"] + fees_usd,
                     row["total_fees_eur"]  + fees_eur, ticker),
                )

    # ── Positions ─────────────────────────────────────────────────────────────

    # ── Cash ──────────────────────────────────────────────────────────────────

    def seed_cash(self, amount_eur: float) -> None:
        """Set the paper cash balance. Call once to initialise; safe to call again."""
        if amount_eur < 0:
            raise ValueError(f"amount_eur must be non-negative, got {amount_eur}")
        self._conn.execute(
            "UPDATE portfolio_cash SET balance_eur = ?, updated = ? WHERE id = 1",
            (amount_eur, _today()),
        )
        self._conn.commit()

    def get_cash_eur(self) -> float:
        """Return current paper cash balance in EUR."""
        row = self._conn.execute(
            "SELECT balance_eur FROM portfolio_cash WHERE id = 1"
        ).fetchone()
        return float(row["balance_eur"]) if row else 0.0

    def adjust_cash(self, delta_eur: float) -> None:
        """Add or subtract *delta_eur* from cash. Silently floors at 0 (paper trading)."""
        self._conn.execute(
            "UPDATE portfolio_cash SET balance_eur = MAX(0.0, balance_eur + ?), updated = ? WHERE id = 1",
            (delta_eur, _today()),
        )
        self._conn.commit()

    def get_positions(self) -> dict[str, dict]:
        rows = self._conn.execute("SELECT * FROM positions").fetchall()
        return {
            r["ticker"]: {
                "qty":                r["qty"],
                "avg_cost_basis":     r["avg_cost_basis"],
                "avg_cost_basis_eur": r["avg_cost_basis_eur"] or 0.0,
                "total_fees_paid":    r["total_fees_paid"],
                "total_fees_eur":     r["total_fees_eur"] or 0.0,
                "first_buy_date":     r["first_buy_date"],
            }
            for r in rows
        }

    # ── Performance ───────────────────────────────────────────────────────────

    def record_performance(
        self,
        portfolio_value_eur: float,
        benchmark_value: float,
        cash_eur: float,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO performance
               (date, portfolio_value_eur, benchmark_value, cash_eur)
               VALUES (?, ?, ?, ?)""",
            (_today(), portfolio_value_eur, benchmark_value, cash_eur),
        )
        self._conn.commit()

    def get_performance_history(self, limit: int = 52) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM performance ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Forecasts ─────────────────────────────────────────────────────────────

    def save_forecast(
        self,
        month: str,          # "YYYY-MM"
        expected_low: float,
        expected_high: float,
        notes: str = "",
    ) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO forecasts
               (month, expected_low, expected_high, notes)
               VALUES (?, ?, ?, ?)""",
            (month, expected_low, expected_high, notes),
        )
        self._conn.commit()

    def update_forecast_actual(
        self,
        month: str,
        actual: float,
        benchmark_actual: float,
    ) -> None:
        self._conn.execute(
            "UPDATE forecasts SET actual=?, benchmark_actual=? WHERE month=?",
            (actual, benchmark_actual, month),
        )
        self._conn.commit()

    def get_forecast(self, month: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM forecasts WHERE month=?", (month,)
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self._conn.close()


# ── Utilities ─────────────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def rules_hash(rules_path: str | Path) -> str:
    """SHA-256 of rules.yaml — stored with every decision for full audit trail."""
    content = Path(rules_path).read_bytes()
    return hashlib.sha256(content).hexdigest()[:16]
