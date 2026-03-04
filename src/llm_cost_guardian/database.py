"""Database module for tracking LLM API costs."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass
class CostRecord:
    """Record of an LLM API call cost."""
    
    id: int | None
    user_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    prompt_hash: str
    timestamp: datetime
    request_id: str | None
    response_time_ms: int | None


class CostDatabase:
    """SQLite database for tracking LLM costs."""
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None
    
    async def init(self) -> None:
        """Initialize database and create tables."""
        self._conn = await aiosqlite.connect(str(self.db_path))
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                cost REAL NOT NULL,
                prompt_hash TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                request_id TEXT,
                response_time_ms INTEGER
            )
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id ON costs(user_id)
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_model ON costs(model)
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON costs(timestamp)
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompt_hash ON costs(prompt_hash)
        """)
        await self._conn.commit()
    
    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
    
    async def record_cost(
        self,
        user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        prompt: str,
        request_id: str | None = None,
        response_time_ms: int | None = None,
    ) -> CostRecord:
        """Record a cost entry."""
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        timestamp = datetime.now(timezone.utc).isoformat()
        
        cursor = await self._conn.execute(
            """INSERT INTO costs 
               (user_id, model, prompt_tokens, completion_tokens, cost, prompt_hash, timestamp, request_id, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, model, prompt_tokens, completion_tokens, cost, prompt_hash, timestamp, request_id, response_time_ms),
        )
        await self._conn.commit()
        
        return CostRecord(
            id=cursor.lastrowid,
            user_id=user_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            prompt_hash=prompt_hash,
            timestamp=datetime.fromisoformat(timestamp),
            request_id=request_id,
            response_time_ms=response_time_ms,
        )
    
    async def get_user_daily_spend(self, user_id: str) -> float:
        """Get total spend for a user today."""
        today = datetime.now(timezone.utc).date().isoformat()
        cursor = await self._conn.execute(
            """SELECT COALESCE(SUM(cost), 0) FROM costs 
               WHERE user_id = ? AND timestamp >= ?""",
            (user_id, today),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0
    
    async def get_model_daily_spend(self, model: str) -> float:
        """Get total spend for a model today."""
        today = datetime.now(timezone.utc).date().isoformat()
        cursor = await self._conn.execute(
            """SELECT COALESCE(SUM(cost), 0) FROM costs 
               WHERE model = ? AND timestamp >= ?""",
            (model, today),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0
    
    async def get_global_daily_spend(self) -> float:
        """Get total global spend today."""
        today = datetime.now(timezone.utc).date().isoformat()
        cursor = await self._conn.execute(
            """SELECT COALESCE(SUM(cost), 0) FROM costs 
               WHERE timestamp >= ?""",
            (today,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0.0
    
    async def get_user_request_count(self, user_id: str, minutes: int = 1) -> int:
        """Get request count for user in the last N minutes."""
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        cursor = await self._conn.execute(
            """SELECT COUNT(*) FROM costs 
               WHERE user_id = ? AND timestamp >= ?""",
            (user_id, since),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
    
    async def get_top_users(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top users by spend."""
        today = datetime.now(timezone.utc).date().isoformat()
        cursor = await self._conn.execute(
            """SELECT user_id, SUM(cost) as total, COUNT(*) as requests
               FROM costs WHERE timestamp >= ?
               GROUP BY user_id ORDER BY total DESC LIMIT ?""",
            (today, limit),
        )
        rows = await cursor.fetchall()
        return [
            {"user_id": row[0], "total_cost": row[1], "request_count": row[2]}
            for row in rows
        ]
    
    async def get_top_models(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top models by spend."""
        today = datetime.now(timezone.utc).date().isoformat()
        cursor = await self._conn.execute(
            """SELECT model, SUM(cost) as total, COUNT(*) as requests
               FROM costs WHERE timestamp >= ?
               GROUP BY model ORDER BY total DESC LIMIT ?""",
            (today, limit),
        )
        rows = await cursor.fetchall()
        return [
            {"model": row[0], "total_cost": row[1], "request_count": row[2]}
            for row in rows
        ]
    
    async def get_top_prompts(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top prompts by cost."""
        today = datetime.now(timezone.utc).date().isoformat()
        cursor = await self._conn.execute(
            """SELECT prompt_hash, SUM(cost) as total, COUNT(*) as requests
               FROM costs WHERE timestamp >= ?
               GROUP BY prompt_hash ORDER BY total DESC LIMIT ?""",
            (today, limit),
        )
        rows = await cursor.fetchall()
        return [
            {"prompt_hash": row[0], "total_cost": row[1], "request_count": row[2]}
            for row in rows
        ]
    
    async def get_spending_history(self, days: int = 7) -> list[dict[str, Any]]:
        """Get daily spending for the last N days."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        cursor = await self._conn.execute(
            """SELECT DATE(timestamp) as date, SUM(cost) as total
               FROM costs WHERE timestamp >= ?
               GROUP BY DATE(timestamp) ORDER BY date""",
            (since,),
        )
        rows = await cursor.fetchall()
        return [{"date": row[0], "total_cost": row[1]} for row in rows]
