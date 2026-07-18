"""EMIR trade-reporting semantic model (regulated-industry fixture).

Simplified but structurally honest EMIR REFIT reporting schema. It
deliberately encodes three semantics-dependent failure modes that plain
DDL cannot express: bridge fan-out via the counterparty role table, sign
conventions (signed valuations vs. unsigned posted/received collateral),
and amount ambiguity (notional vs. valuation vs. collateral).
"""

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from semantido import SemanticDeclarativeBase, semantic_table
from semantido.generators.semantic_layer import PrivacyLevel, TimeGrain


@semantic_table(
    description="Legal entity master for derivative counterparties, one row per LEI.",
    business_context=(
        "EMIR counterparty classification drives clearing and reporting "
        "obligations (FC / NFC+ / NFC- under Art. 2 and Art. 10)."
    ),
    synonyms=["legal entities", "counterparties"],
)
class Counterparty(SemanticDeclarativeBase):
    __tablename__ = "counterparty"

    lei = Column(String(20), primary_key=True)
    lei_description = (
        "Legal Entity Identifier (ISO 17442). The sole join key for "
        "counterparty identity everywhere in this schema."
    )
    lei_sample_values = ["529900T8BM49AURSDO55"]

    legal_name = Column(String(200), nullable=False)
    legal_name_description = "Registered legal name as per GLEIF record."
    legal_name_privacy_level = PrivacyLevel.CONFIDENTIAL

    classification = Column(String(4), nullable=False)
    classification_description = (
        "EMIR counterparty classification: 'FC' (financial counterparty, Art. 2(8)), "
        "'NFC+' (non-financial above clearing threshold, Art. 10), 'NFC-' (below). "
        "Filter on this before any obligation question."
    )
    classification_sample_values = ["FC", "NFC+"]

    country = Column(String(2), nullable=False)
    country_description = "ISO 3166-1 alpha-2 country of incorporation."

    roles = relationship("CounterpartyTradeRole", back_populates="counterparty")
    roles_relationship_description = "Roles this entity plays on trades."


@semantic_table(
    description=(
        "Reportable OTC derivative trades, one row per UTI. Immutable birth "
        "record of the trade; evolving economics live in trade_state."
    ),
    business_context=(
        "AMBIGUITY GUARD: notional_amount is contract size, NOT current value "
        "and NOT exposure. 'How big' -> notional; 'worth today' -> "
        "trade_state.valuation_amount; 'at risk' -> collateral_report."
    ),
    synonyms=["trades", "derivatives", "positions"],
    time_dimension="execution_timestamp",
)
class Trade(SemanticDeclarativeBase):
    __tablename__ = "trade"

    uti = Column(String(52), primary_key=True)
    uti_description = "Unique Trade Identifier (Art. 9 REFIT). Canonical trade key."

    asset_class = Column(String(4), nullable=False)
    asset_class_description = (
        "EMIR asset class: 'IR' rates, 'CR' credit, 'EQ' equity, 'FX' FX, 'CO' commodity."
    )
    asset_class_sample_values = ["IR", "FX"]

    notional_amount = Column(Numeric(20, 2), nullable=False)
    notional_amount_description = (
        "Trade notional in notional_currency. Contract size, not value. Never "
        "sum across currencies without conversion."
    )
    notional_amount_privacy_level = PrivacyLevel.RESTRICTED

    notional_currency = Column(String(3), nullable=False)
    notional_currency_description = "ISO 4217 currency of notional_amount."
    notional_currency_sample_values = ["EUR", "USD"]

    execution_timestamp = Column(DateTime, nullable=False)
    execution_timestamp_description = "UTC execution time (Art. 9 REFIT). Primary time axis."
    execution_timestamp_time_grain = TimeGrain.SECOND

    states = relationship("TradeState", back_populates="trade")
    states_relationship_description = "Daily state reports for this trade."
    roles = relationship("CounterpartyTradeRole", back_populates="trade")
    roles_relationship_description = "Counterparty roles attached to this trade."


@semantic_table(
    description=(
        "Daily trade-state reports (EMIR REFIT is state-based), one row per "
        "UTI per reporting_date. The latest state per UTI is the current view."
    ),
    business_context=(
        "SIGN CONVENTION: valuation_amount is signed from the REPORTING "
        "counterparty's perspective. Aggregating across counterparties without "
        "flipping signs by role is meaningless."
    ),
    sql_filters=[
        "reporting_date = (SELECT MAX(ts2.reporting_date) FROM trade_state ts2 "
        "WHERE ts2.uti = trade_state.uti)"
    ],
    time_dimension="reporting_date",
)
class TradeState(SemanticDeclarativeBase):
    __tablename__ = "trade_state"

    id = Column(Integer, primary_key=True)
    uti = Column(String(52), ForeignKey("trade.uti"), nullable=False)
    reporting_date = Column(Date, nullable=False)
    reporting_date_description = "State date; one state per trade per day."
    reporting_date_time_grain = TimeGrain.DAY

    valuation_amount = Column(Numeric(20, 2))
    valuation_amount_description = (
        "Mark-to-market value (Art. 11(2)), signed from the reporting "
        "counterparty's perspective. Value, not size: do not confuse with notional."
    )
    valuation_amount_privacy_level = PrivacyLevel.RESTRICTED

    contract_status = Column(String(4), nullable=False)
    contract_status_description = (
        "'OUTS' outstanding, 'TERM' terminated, 'MATU' matured, 'ERRO' error. "
        "Exclude non-OUTS from exposure aggregates unless asked otherwise."
    )
    contract_status_sample_values = ["OUTS", "TERM"]

    trade = relationship("Trade", back_populates="states")
    trade_relationship_description = "The trade this state report belongs to."


@semantic_table(
    description=(
        "Role bridge between trades and counterparties. A trade has AT LEAST "
        "two rows here (reporting + other counterparty), often more."
    ),
    business_context=(
        "FAN-OUT GUARD: joining trade -> this table -> counterparty multiplies "
        "trade rows by role count; any SUM over trade amounts after this join "
        "double-counts. Filter to a single role (usually 'RPTG') before "
        "aggregating trade-level amounts."
    ),
)
class CounterpartyTradeRole(SemanticDeclarativeBase):
    __tablename__ = "counterparty_trade_role"

    id = Column(Integer, primary_key=True)
    uti = Column(String(52), ForeignKey("trade.uti"), nullable=False)
    lei = Column(String(20), ForeignKey("counterparty.lei"), nullable=False)

    role = Column(String(4), nullable=False)
    role_description = (
        "'RPTG' reporting counterparty, 'OTHR' other counterparty, 'BRKR' "
        "broker, 'CLRM' clearing member. Exactly one RPTG row per UTI."
    )
    role_sample_values = ["RPTG", "OTHR"]

    trade = relationship("Trade", back_populates="roles")
    trade_relationship_description = "Trade this role row belongs to."
    counterparty = relationship("Counterparty", back_populates="roles")
    counterparty_relationship_description = "Entity playing this role."


@semantic_table(
    description="Daily collateral/margin per UTI (Art. 11(3)).",
    business_context=(
        "SIGN CONVENTION (different from trade_state!): amounts are UNSIGNED; "
        "direction is carried by which column they sit in. Net collateral = "
        "posted - received, computed, never read from a column."
    ),
    time_dimension="reporting_date",
)
class CollateralReport(SemanticDeclarativeBase):
    __tablename__ = "collateral_report"

    id = Column(Integer, primary_key=True)
    uti = Column(String(52), ForeignKey("trade.uti"), nullable=False)
    reporting_date = Column(Date, nullable=False)
    reporting_date_time_grain = TimeGrain.DAY

    initial_margin_posted = Column(Numeric(20, 2))
    initial_margin_posted_description = "IM posted BY the reporting counterparty. Unsigned."
    initial_margin_received = Column(Numeric(20, 2))
    initial_margin_received_description = (
        "IM received. Unsigned; do not sum with posted — subtract."
    )
    collateral_currency = Column(String(3))
    collateral_currency_description = "ISO 4217 currency of margin amounts."


@semantic_table(
    description=(
        "Trade repository submission log. Grain: one row per submission "
        "attempt — a trade may have many."
    ),
    business_context=(
        "Rejection-rate questions resolve here, per attempt, not per trade, "
        "unless deduplicated. Rejection rate = NACK / all attempts."
    ),
    time_dimension="submission_timestamp",
)
class SubmissionLog(SemanticDeclarativeBase):
    __tablename__ = "submission_log"

    id = Column(Integer, primary_key=True)
    uti = Column(String(52), ForeignKey("trade.uti"), nullable=False)
    submission_timestamp = Column(DateTime, nullable=False)
    submission_timestamp_time_grain = TimeGrain.SECOND

    status = Column(String(4), nullable=False)
    status_description = "'ACKD' accepted, 'NACK' rejected, 'PEND' pending."
    status_sample_values = ["ACKD", "NACK"]

    nack_reason = Column(String(200))
    nack_reason_description = "TR rejection reason text; NULL unless NACK."
