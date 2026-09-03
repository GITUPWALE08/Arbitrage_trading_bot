import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, Any, List

from sqlalchemy import Column, String, Float, DateTime, Boolean, Integer, JSON
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.dialects.postgresql import JSONB

from core.state_store import StateStore
from core.execution_engine import ExecutionState
from core.logger import logging

logger = logging.getLogger("Database")
logger.setLevel(logging.INFO)

Base = declarative_base()

class ExecutionRecord(Base):
    __tablename__ = "executions"
    execution_id = Column(String, primary_key=True)
    strategy = Column(String, nullable=False)
    state = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    realized_profit = Column(Float, default=0.0)
    data = Column(JSON, default=dict) # Use JSON to support SQLite testing and Postgres JSONB gracefully
    mode = Column(String, default="simulated")

class ReconciliationLog(Base):
    __tablename__ = "reconciliation_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    exchange = Column(String, nullable=False)
    expected_balances = Column(JSON, default=dict)
    actual_balances = Column(JSON, default=dict)
    discrepancies = Column(JSON, default=dict)
    severity = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    mode = Column(String, default="simulated")

class KillSwitchRecord(Base):
    __tablename__ = "kill_switches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String, nullable=False)
    scope_value = Column(String, nullable=True)
    is_tripped = Column(Boolean, default=False)
    tripped_by = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class ExecutionLeg(Base):
    __tablename__ = "execution_legs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String, nullable=False)
    leg_number = Column(Integer, nullable=False)
    exchange = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    intended_qty = Column(Float, nullable=False)
    filled_qty = Column(Float, default=0.0)
    avg_fill_price = Column(Float, nullable=True)
    fee_paid = Column(Float, default=0.0)
    order_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    submitted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    filled_at = Column(DateTime(timezone=True), nullable=True)
    mode = Column(String, default="simulated")

class OpportunityRecord(Base):
    __tablename__ = "opportunities"
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy = Column(String, nullable=False)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    symbols = Column(String, nullable=False)
    gross_spread_pct = Column(Float, nullable=False)
    net_profit_estimate = Column(Float, nullable=False)
    fee_breakdown = Column(JSON, default=dict)
    threshold_at_time = Column(Float, nullable=False)
    action_taken = Column(String, nullable=False)
    execution_id = Column(String, nullable=True)
    mode = Column(String, default="simulated")

class BalancesSnapshot(Base):
    __tablename__ = "balances_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    exchange = Column(String, nullable=False)
    asset = Column(String, nullable=False)
    balance = Column(Float, nullable=False)
    snapshot_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    source = Column(String, nullable=False)
    mode = Column(String, default="simulated")

class FundingRateHistory(Base):
    __tablename__ = "funding_rate_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    exchange = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    rate = Column(Float, nullable=False)
    annualized_pct = Column(Float, nullable=False)
    recorded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class SystemEvent(Base):
    __tablename__ = "system_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    mode = Column(String, default="simulated")

class MarginMonitoring(Base):
    __tablename__ = "margin_monitoring"
    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(String, nullable=False)
    exchange = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    margin_ratio = Column(Float, nullable=False)
    liquidation_price = Column(Float, nullable=True)
    checked_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    mode = Column(String, default="simulated")


class SystemSettings(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)

class DatabaseStateStore(StateStore):
    """
    SQLAlchemy-based Postgres/SQLite implementation of StateStore per Section 10.
    """
    def __init__(self, db_url: str, active_mode: str = 'simulated'):
        self.active_mode = active_mode
        # We use aiosqlite for tests, asyncpg for real DB
        self.engine = create_async_engine(db_url, echo=False)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        
    async def initialize_db(self):
        # 1. Create tables outside of the migration transaction
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        # 2. Run migrations one by one in autocommit mode or separate blocks
        tables = ["executions", "reconciliation_log", "execution_legs", "opportunities", "balances_snapshot", "system_events", "margin_monitoring"]
        from sqlalchemy import text as sqa_text
        for table in tables:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(sqa_text(f"ALTER TABLE {table} ADD COLUMN mode VARCHAR DEFAULT 'simulated'"))
            except Exception:
                pass


    async def get_system_setting(self, key: str) -> str:
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(SystemSettings).where(SystemSettings.key == key)
            result = await session.execute(stmt)
            record = result.scalars().first()
            return record.value if record else None

    async def set_system_setting(self, key: str, value: str):
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(SystemSettings).where(SystemSettings.key == key)
            result = await session.execute(stmt)
            record = result.scalars().first()
            if not record:
                record = SystemSettings(key=key, value=value)
                session.add(record)
            else:
                record.value = value
            await session.commit()
            
    async def delete_system_setting(self, key: str):
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(SystemSettings).where(SystemSettings.key == key)
            result = await session.execute(stmt)
            record = result.scalars().first()
            if record:
                await session.delete(record)
                await session.commit()

    async def save_execution_state(self, execution_id: str, strategy: str, state: ExecutionState, data: dict):
        async with self.SessionLocal() as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.execution_id == execution_id, ExecutionRecord.mode == self.active_mode)
            result = await session.execute(stmt)
            record = result.scalars().first()
            
            if not record:
                record = ExecutionRecord(mode=self.active_mode, 
                    execution_id=execution_id,
                    strategy=strategy,
                    state=state.name,
                    data=data
                )
                session.add(record)
            else:
                record.state = state.name
                record.data = data
                
            # If completed, check for realized_profit in data
            if state == ExecutionState.COMPLETED and "realized_profit" in data:
                record.realized_profit = float(data["realized_profit"])
                
            await session.commit()
            
    async def get_execution_state(self, execution_id: str) -> dict:
        mode = self.active_mode
        async with self.SessionLocal() as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.execution_id == execution_id, ExecutionRecord.mode == mode)
            result = await session.execute(stmt)
            record = result.scalars().first()
            if not record:
                return None
            return {
                "execution_id": record.execution_id,
                "strategy": record.strategy,
                "state": ExecutionState[record.state],
                "data": record.data,
                "realized_profit": record.realized_profit
            }

    async def get_active_executions(self) -> List[Dict[str, Any]]:
        mode = self.active_mode
        active_states = [
            ExecutionState.VALIDATING.name, 
            ExecutionState.EXECUTING_LEG_1.name, 
            ExecutionState.EXECUTING_LEG_2.name,
            ExecutionState.EXECUTING_LEG_3.name,
            ExecutionState.CONFIRMING_FILLS.name,
            ExecutionState.PARTIAL_FAILURE.name,
            ExecutionState.UNWINDING.name,
            ExecutionState.STUCK.name
        ]
        
        async with self.SessionLocal() as session:
            stmt = select(ExecutionRecord).where(ExecutionRecord.state.in_(active_states), ExecutionRecord.mode == mode)
            result = await session.execute(stmt)
            records = result.scalars().all()
            
            return [{
                "execution_id": r.execution_id,
                "strategy": r.strategy,
                "state": r.state,
                "data": r.data,
                "created_at": r.created_at
            } for r in records]
            
    async def get_expected_balances(self, exchange: str) -> dict:
        mode = getattr(self, "active_mode", "simulated")
        async with self.SessionLocal() as session:
            stmt = select(BalancesSnapshot).where(
                BalancesSnapshot.exchange == exchange,
                BalancesSnapshot.mode == mode
            ).order_by(BalancesSnapshot.snapshot_at.asc())
            result = await session.execute(stmt)
            records = result.scalars().all()
            
            balances = {}
            for r in records:
                balances[r.asset] = r.balance
            return balances
        
    async def save_reconciliation_log(self, exchange: str, expected: dict, actual: dict, discrepancies: dict, severity: str):
        async with self.SessionLocal() as session:
            log = ReconciliationLog(mode=self.active_mode, 
                exchange=exchange,
                expected_balances=expected,
                actual_balances=actual,
                discrepancies=discrepancies,
                severity=severity
            )
            session.add(log)
            await session.commit()

    async def get_kill_switch(self, scope: str, scope_value: str = None) -> dict:
        async with self.SessionLocal() as session:
            stmt = select(KillSwitchRecord).where(
                KillSwitchRecord.scope == scope,
                KillSwitchRecord.scope_value == scope_value
            )
            result = await session.execute(stmt)
            record = result.scalars().first()
            if not record:
                return None
                
            return {
                "scope": record.scope,
                "scope_value": record.scope_value,
                "is_tripped": record.is_tripped,
                "tripped_by": record.tripped_by,
                "reason": record.reason
            }
            
    async def set_kill_switch(self, scope: str, scope_value: str, is_tripped: bool, tripped_by: str, reason: str):
        async with self.SessionLocal() as session:
            stmt = select(KillSwitchRecord).where(
                KillSwitchRecord.scope == scope,
                KillSwitchRecord.scope_value == scope_value
            )
            result = await session.execute(stmt)
            record = result.scalars().first()
            
            if not record:
                record = KillSwitchRecord(
                    scope=scope,
                    scope_value=scope_value,
                    is_tripped=is_tripped,
                    tripped_by=tripped_by,
                    reason=reason
                )
                session.add(record)
            else:
                record.is_tripped = is_tripped
                record.tripped_by = tripped_by
                record.reason = reason
                
            await session.commit()

    async def save_execution_leg(self, leg_data: dict):
        async with self.SessionLocal() as session:
            record = ExecutionLeg(**leg_data, mode=self.active_mode)
            session.add(record)
            await session.commit()
            
    async def save_opportunity(self, opp_data: dict):
        async with self.SessionLocal() as session:
            record = OpportunityRecord(**opp_data, mode=self.active_mode)
            session.add(record)
            await session.commit()
            
    async def save_balances_snapshot(self, exchange: str, asset: str, balance: float, source: str):
        async with self.SessionLocal() as session:
            record = BalancesSnapshot(mode=self.active_mode, exchange=exchange, asset=asset, balance=balance, source=source)
            session.add(record)
            await session.commit()
            
    async def save_margin_monitoring(self, position_id: str, exchange: str, symbol: str, margin_ratio: float, liquidation_price: float = None):
        async with self.SessionLocal() as session:
            record = MarginMonitoring(mode=self.active_mode, position_id=position_id, exchange=exchange, symbol=symbol, margin_ratio=margin_ratio, liquidation_price=liquidation_price)
            session.add(record)
            await session.commit()
            
    async def save_funding_rate(self, exchange: str, symbol: str, rate: float, annualized_pct: float):
        async with self.SessionLocal() as session:
            record = FundingRateHistory(exchange=exchange, symbol=symbol, rate=rate, annualized_pct=annualized_pct)
            session.add(record)
            await session.commit()

    async def get_pnl(self, mode: str = None) -> float:
        mode = mode or getattr(self, "active_mode", "simulated")
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(ExecutionRecord).where(
                ExecutionRecord.state == "COMPLETED",
                ExecutionRecord.mode == mode
            )
            result = await session.execute(stmt)
            records = result.scalars().all()
            return sum([r.realized_profit for r in records if r.realized_profit])
            
    async def get_pnl_by_strategy(self, mode: str = None) -> dict:
        mode = mode or getattr(self, "active_mode", "simulated")
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(ExecutionRecord).where(
                ExecutionRecord.state == "COMPLETED",
                ExecutionRecord.mode == mode
            )
            result = await session.execute(stmt)
            records = result.scalars().all()
            
            pnl_map = {}
            for r in records:
                if r.realized_profit:
                    pnl_map[r.strategy] = pnl_map.get(r.strategy, 0.0) + r.realized_profit
            return pnl_map

    async def get_latest_balances(self) -> dict:
        async with self.SessionLocal() as session:
            from sqlalchemy import select, desc
            stmt = select(BalancesSnapshot).order_by(desc(BalancesSnapshot.snapshot_at))
            result = await session.execute(stmt)
            records = result.scalars().all()
            # Just group by exchange and asset (naive version for MVP)
            bals = {}
            for r in records:
                if r.exchange not in bals: bals[r.exchange] = {}
                if r.asset not in bals[r.exchange]: bals[r.exchange][r.asset] = r.balance
            return bals

    async def get_recent_opportunities(self) -> list:
        async with self.SessionLocal() as session:
            from sqlalchemy import select, desc
            stmt = select(OpportunityRecord).order_by(desc(OpportunityRecord.id)).limit(5)
            result = await session.execute(stmt)
            return [{"strategy": r.strategy, "gross_profit": r.gross_profit, "net_profit": r.net_profit} for r in result.scalars().all()]

    async def get_recent_executions(self) -> list:
        async with self.SessionLocal() as session:
            from sqlalchemy import select, desc
            stmt = select(ExecutionRecord).order_by(desc(ExecutionRecord.id)).limit(5)
            result = await session.execute(stmt)
            return [{"execution_id": r.execution_id, "strategy": r.strategy, "state": r.state, "profit": r.realized_profit} for r in result.scalars().all()]

    async def get_all_kill_switches(self) -> list:
        async with self.SessionLocal() as session:
            from sqlalchemy import select
            stmt = select(KillSwitchRecord)
            result = await session.execute(stmt)
            return [{"scope": r.scope, "scope_value": r.scope_value, "is_tripped": r.is_tripped} for r in result.scalars().all()]
