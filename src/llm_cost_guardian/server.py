"""
FastAPI server for LLM Cost Guardian - Live Dashboard API
"""

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="LLM Cost Guardian API",
    description="Real-time cost monitoring and circuit-breaker for LLM APIs",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database path
DB_PATH = Path.home() / ".llm-cost-guardian" / "costs.db"


# Models
class CostSummary(BaseModel):
    total_spend: float
    request_count: int
    active_tenants: int
    avg_cost_per_request: float
    circuit_breaker_status: str


class TenantCost(BaseModel):
    tenant_id: str
    total_spend: float
    request_count: int
    model_breakdown: dict


class AgentTrace(BaseModel):
    trace_id: str
    tenant_id: str
    agent_name: str
    start_time: datetime
    end_time: datetime | None
    total_cost: float
    request_count: int
    status: str


class AlertConfig(BaseModel):
    threshold: int
    webhook_url: str | None
    enabled: bool = True


class SpendingAlert(BaseModel):
    id: int
    tenant_id: str | None
    threshold: int
    current_spend: float
    limit: float
    triggered_at: datetime
    acknowledged: bool = False


@app.get("/")
async def root():
    return {"message": "LLM Cost Guardian API", "version": "0.1.0"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/costs/summary", response_model=CostSummary)
async def get_cost_summary():
    """Get global cost summary for the dashboard"""
    if not DB_PATH.exists():
        return CostSummary(
            total_spend=0.0,
            request_count=0,
            active_tenants=0,
            avg_cost_per_request=0.0,
            circuit_breaker_status="closed"
        )

    async with aiosqlite.connect(DB_PATH) as db:
        # Get total spend and request count
        cursor = await db.execute("""
            SELECT COALESCE(SUM(total_cost), 0) as total,
                   COUNT(*) as count,
                   COUNT(DISTINCT tenant_id) as tenants
            FROM requests
            WHERE timestamp >= datetime('now', '-24 hours')
        """)
        row = await cursor.fetchone()

        total_spend = row[0] if row[0] else 0.0
        request_count = row[1] if row[1] else 0
        active_tenants = row[2] if row[2] else 0
        avg_cost = total_spend / request_count if request_count > 0 else 0.0

        # Get circuit breaker status
        cursor = await db.execute("""
            SELECT status FROM circuit_breakers
            ORDER BY triggered_at DESC LIMIT 1
        """)
        row = await cursor.fetchone()
        cb_status = row[0] if row else "closed"

    return CostSummary(
        total_spend=round(total_spend, 4),
        request_count=request_count,
        active_tenants=active_tenants,
        avg_cost_per_request=round(avg_cost, 4),
        circuit_breaker_status=cb_status
    )


@app.get("/api/costs/tenants", response_model=list[TenantCost])
async def get_tenant_costs(limit: int = Query(10, ge=1, le=100)):
    """Get per-tenant cost attribution"""
    if not DB_PATH.exists():
        return []

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT tenant_id,
                   SUM(total_cost) as total,
                   COUNT(*) as count
            FROM requests
            WHERE timestamp >= datetime('now', '-24 hours')
            GROUP BY tenant_id
            ORDER BY total DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()

    results = []
    for row in rows:
        tenant_id = row[0]
        # Get model breakdown
        model_breakdown = await get_model_breakdown(tenant_id)
        results.append(TenantCost(
            tenant_id=tenant_id,
            total_spend=round(row[1] or 0.0, 4),
            request_count=row[2] or 0,
            model_breakdown=model_breakdown
        ))

    return results


async def get_model_breakdown(tenant_id: str) -> dict:
    """Get cost breakdown by model for a tenant"""
    if not DB_PATH.exists():
        return {}

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT model, SUM(total_cost) as cost, COUNT(*) as count
            FROM requests
            WHERE tenant_id = ? AND timestamp >= datetime('now', '-24 hours')
            GROUP BY model
        """, (tenant_id,))
        rows = await cursor.fetchall()

    return {row[0]: {"cost": round(row[1] or 0.0, 4), "requests": row[2] or 0}
            for row in rows}


@app.get("/api/traces", response_model=list[AgentTrace])
async def get_agent_traces(
    tenant_id: str | None = None,
    limit: int = Query(50, ge=1, le=500)
):
    """Get agent trace tracking"""
    if not DB_PATH.exists():
        return []

    async with aiosqlite.connect(DB_PATH) as db:
        if tenant_id:
            cursor = await db.execute("""
                SELECT trace_id, tenant_id, agent_name, start_time, end_time,
                       total_cost, request_count, status
                FROM agent_traces
                WHERE tenant_id = ?
                ORDER BY start_time DESC
                LIMIT ?
            """, (tenant_id, limit))
        else:
            cursor = await db.execute("""
                SELECT trace_id, tenant_id, agent_name, start_time, end_time,
                       total_cost, request_count, status
                FROM agent_traces
                ORDER BY start_time DESC
                LIMIT ?
            """, (limit,))
        rows = await cursor.fetchall()

    return [AgentTrace(
        trace_id=row[0],
        tenant_id=row[1],
        agent_name=row[2],
        start_time=datetime.fromisoformat(row[3]) if isinstance(row[3], str) else row[3],
        end_time=datetime.fromisoformat(row[4]) if row[4] and isinstance(row[4], str) else row[4],
        total_cost=round(row[5] or 0.0, 4),
        request_count=row[6] or 0,
        status=row[7]
    ) for row in rows]


@app.get("/api/alerts", response_model=list[SpendingAlert])
async def get_alerts(unacknowledged_only: bool = False):
    """Get spending alerts"""
    if not DB_PATH.exists():
        return []

    async with aiosqlite.connect(DB_PATH) as db:
        if unacknowledged_only:
            cursor = await db.execute("""
                SELECT id, tenant_id, threshold, current_spend, limit, triggered_at, acknowledged
                FROM alerts
                WHERE acknowledged = 0
                ORDER BY triggered_at DESC
            """)
        else:
            cursor = await db.execute("""
                SELECT id, tenant_id, threshold, current_spend, limit, triggered_at, acknowledged
                FROM alerts
                ORDER BY triggered_at DESC
                LIMIT 50
            """)
        rows = await cursor.fetchall()

    return [SpendingAlert(
        id=row[0],
        tenant_id=row[1],
        threshold=row[2],
        current_spend=round(row[3] or 0.0, 4),
        limit=round(row[4] or 0.0, 4),
        triggered_at=datetime.fromisoformat(row[5]) if isinstance(row[5], str) else row[5],
        acknowledged=bool(row[6])
    ) for row in rows]


@app.post("/api/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int):
    """Acknowledge an alert"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE alerts SET acknowledged = 1 WHERE id = ?",
            (alert_id,)
        )
        await db.commit()

    return {"status": "acknowledged", "alert_id": alert_id}


@app.get("/api/costs/history")
async def get_cost_history(
    tenant_id: str | None = None,
    hours: int = Query(24, ge=1, le=168)
):
    """Get cost history for charts (hourly buckets)"""
    if not DB_PATH.exists():
        return []

    async with aiosqlite.connect(DB_PATH) as db:
        if tenant_id:
            cursor = await db.execute("""
                SELECT strftime('%Y-%m-%d %H:00:00', timestamp) as hour,
                       SUM(total_cost) as cost,
                       COUNT(*) as requests
                FROM requests
                WHERE tenant_id = ?
                  AND timestamp >= datetime('now', '-' || ? || ' hours')
                GROUP BY hour
                ORDER BY hour ASC
            """, (tenant_id, hours))
        else:
            cursor = await db.execute("""
                SELECT strftime('%Y-%m-%d %H:00:00', timestamp) as hour,
                       SUM(total_cost) as cost,
                       COUNT(*) as requests
                FROM requests
                WHERE timestamp >= datetime('now', '-' || ? || ' hours')
                GROUP BY hour
                ORDER BY hour ASC
            """, (hours,))
        rows = await cursor.fetchall()

    return [{"hour": row[0], "cost": round(row[1] or 0.0, 4), "requests": row[2] or 0}
            for row in rows]


@app.get("/api/circuit-breaker/status")
async def get_circuit_breaker_status():
    """Get circuit breaker status"""
    if not DB_PATH.exists():
        return {"status": "closed", "failure_count": 0, "last_failure": None}

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT status, failure_count, last_failure, triggered_at
            FROM circuit_breakers
            ORDER BY triggered_at DESC
            LIMIT 1
        """)
        row = await cursor.fetchone()

    if not row:
        return {"status": "closed", "failure_count": 0, "last_failure": None}

    return {
        "status": row[0],
        "failure_count": row[1] or 0,
        "last_failure": row[2],
        "triggered_at": row[3]
    }


@app.post("/api/circuit-breaker/reset")
async def reset_circuit_breaker():
    """Manually reset the circuit breaker"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="Database not found")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO circuit_breakers (status, failure_count, triggered_at)
            VALUES ('closed', 0, datetime('now'))
        """)
        await db.commit()

    return {"status": "reset", "circuit_breaker_status": "closed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
