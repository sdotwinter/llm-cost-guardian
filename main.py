"""
LLM Cost Guardian - Track and control LLM spending with per-tenant attribution.
"""
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel


# Database setup
DB_PATH = "llm_cost_guardian.db"


def init_db():
    """Initialize SQLite database with required tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tenants table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            api_key TEXT UNIQUE NOT NULL,
            budget_limit REAL DEFAULT 100.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Cost records table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cost_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL,
            model_name TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost REAL NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)
    
    # Alerts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL,
            alert_type TEXT NOT NULL,
            message TEXT NOT NULL,
            threshold REAL NOT NULL,
            current_value REAL NOT NULL,
            triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)
    
    # Circuit breaker state
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS circuit_breakers (
            tenant_id INTEGER PRIMARY KEY,
            state TEXT DEFAULT 'CLOSED',
            failure_count INTEGER DEFAULT 0,
            last_failure TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
    """)
    
    conn.commit()
    conn.close()


# Circuit Breaker States
class CircuitState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# Pydantic models
class TenantCreate(BaseModel):
    name: str
    budget_limit: float = 100.0


class TenantResponse(BaseModel):
    id: int
    name: str
    api_key: str
    budget_limit: float
    created_at: str


class CostRecordCreate(BaseModel):
    model_name: str
    input_tokens: int
    output_tokens: int
    cost: float


class CostRecordResponse(BaseModel):
    id: int
    tenant_id: int
    model_name: str
    input_tokens: int
    output_tokens: int
    cost: float
    timestamp: str


class SpendingResponse(BaseModel):
    tenant_id: int
    tenant_name: str
    total_spent: float
    budget_limit: float
    percent_used: float
    remaining_budget: float


class DashboardResponse(BaseModel):
    total_tenants: int
    total_spending: float
    active_circuits: int
    triggered_alerts: int
    tenant_spending: list[SpendingResponse]


class CircuitBreakerResponse(BaseModel):
    tenant_id: int
    state: str
    failure_count: int
    last_failure: Optional[str]


# In-memory circuit breaker (for quick access)
circuit_breakers = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LLM Cost Guardian", lifespan=lifespan)


# Helper functions
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def check_circuit_breaker(tenant_id: int) -> bool:
    """Check if circuit breaker allows requests. Returns True if allowed."""
    state = circuit_breakers.get(tenant_id, {}).get("state", CircuitState.CLOSED)
    if state == CircuitState.OPEN:
        # Check if we should transition to HALF_OPEN
        cb = circuit_breakers.get(tenant_id, {})
        if cb.get("last_failure"):
            last_failure = datetime.fromisoformat(cb["last_failure"])
            if datetime.now() - last_failure > timedelta(minutes=60):
                circuit_breakers[tenant_id]["state"] = CircuitState.HALF_OPEN
                return True
        return False
    return True


def record_failure(tenant_id: int):
    """Record a failure and potentially open the circuit breaker."""
    if tenant_id not in circuit_breakers:
        circuit_breakers[tenant_id] = {"state": CircuitState.CLOSED, "failure_count": 0, "last_failure": None}
    
    cb = circuit_breakers[tenant_id]
    cb["failure_count"] += 1
    cb["last_failure"] = datetime.now().isoformat()
    
    # Open circuit after 5 consecutive failures
    if cb["failure_count"] >= 5:
        cb["state"] = CircuitState.OPEN


def record_success(tenant_id: int):
    """Record a success and close the circuit breaker."""
    if tenant_id in circuit_breakers:
        circuit_breakers[tenant_id]["state"] = CircuitState.CLOSED
        circuit_breakers[tenant_id]["failure_count"] = 0


def check_spending_alerts(tenant_id: int, total_spent: float, budget_limit: float, background_tasks: BackgroundTasks):
    """Check if spending exceeds thresholds and trigger alerts."""
    percent_used = (total_spent / budget_limit) * 100
    
    alert_triggered = False
    alert_type = ""
    threshold = 0
    message = ""
    
    if percent_used >= 100:
        alert_type = "BUDGET_EXCEEDED"
        threshold = 100
        message = f"Budget exceeded! Spent ${total_spent:.2f} of ${budget_limit:.2f}"
        alert_triggered = True
    elif percent_used >= 90:
        alert_type = "BUDGET_CRITICAL"
        threshold = 90
        message = f"Critical: 90% budget used (${total_spent:.2f}/${budget_limit:.2f})"
        alert_triggered = True
    elif percent_used >= 75:
        alert_type = "BUDGET_WARNING"
        threshold = 75
        message = f"Warning: 75% budget used (${total_spent:.2f}/${budget_limit:.2f})"
        alert_triggered = True
    
    if alert_triggered:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO alerts (tenant_id, alert_type, message, threshold, current_value)
               VALUES (?, ?, ?, ?, ?)""",
            (tenant_id, alert_type, message, threshold, percent_used)
        )
        conn.commit()
        conn.close()


# API Endpoints

@app.post("/tenants", response_model=TenantResponse)
def create_tenant(tenant: TenantCreate):
    """Create a new tenant with API key and budget limit."""
    import secrets
    
    conn = get_db()
    cursor = conn.cursor()
    
    api_key = f"sk_lcg_{secrets.token_urlsafe(32)}"
    
    try:
        cursor.execute(
            "INSERT INTO tenants (name, api_key, budget_limit) VALUES (?, ?, ?)",
            (tenant.name, api_key, tenant.budget_limit)
        )
        tenant_id = cursor.lastrowid
        conn.commit()
        
        cursor.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
        row = cursor.fetchone()
        
        # Initialize circuit breaker
        circuit_breakers[tenant_id] = {"state": CircuitState.CLOSED, "failure_count": 0, "last_failure": None}
        
        conn.close()
        
        return TenantResponse(
            id=row["id"],
            name=row["name"],
            api_key=row["api_key"],
            budget_limit=row["budget_limit"],
            created_at=row["created_at"]
        )
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="API key already exists")


@app.get("/tenants/{tenant_id}", response_model=TenantResponse)
def get_tenant(tenant_id: int):
    """Get tenant details."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    return TenantResponse(
        id=row["id"],
        name=row["name"],
        api_key=row["api_key"],
        budget_limit=row["budget_limit"],
        created_at=row["created_at"]
    )


@app.post("/tenants/{tenant_id}/costs", response_model=CostRecordResponse)
def record_cost(tenant_id: int, cost_record: CostRecordCreate, background_tasks: BackgroundTasks):
    """Record a cost for a tenant."""
    # Check circuit breaker
    if not check_circuit_breaker(tenant_id):
        raise HTTPException(status_code=503, detail="Circuit breaker OPEN - too many failures")
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Verify tenant exists
    cursor.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    tenant = cursor.fetchone()
    if not tenant:
        conn.close()
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Insert cost record
    cursor.execute(
        """INSERT INTO cost_records (tenant_id, model_name, input_tokens, output_tokens, cost)
           VALUES (?, ?, ?, ?, ?)""",
        (tenant_id, cost_record.model_name, cost_record.input_tokens, cost_record.output_tokens, cost_record.cost)
    )
    cost_id = cursor.lastrowid
    conn.commit()
    
    # Get total spent
    cursor.execute("SELECT SUM(cost) as total FROM cost_records WHERE tenant_id = ?", (tenant_id,))
    total_spent = cursor.fetchone()["total"] or 0
    
    cursor.execute("SELECT * FROM cost_records WHERE id = ?", (cost_id,))
    row = cursor.fetchone()
    conn.close()
    
    # Record success
    record_success(tenant_id)
    
    # Check spending alerts
    background_tasks.add_task(check_spending_alerts, tenant_id, total_spent, tenant["budget_limit"])
    
    return CostRecordResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        model_name=row["model_name"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost=row["cost"],
        timestamp=row["timestamp"]
    )


@app.get("/tenants/{tenant_id}/spending", response_model=SpendingResponse)
def get_tenant_spending(tenant_id: int):
    """Get current spending for a tenant."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    tenant = cursor.fetchone()
    if not tenant:
        conn.close()
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    cursor.execute("SELECT SUM(cost) as total FROM cost_records WHERE tenant_id = ?", (tenant_id,))
    total_spent = cursor.fetchone()["total"] or 0
    
    percent_used = (total_spent / tenant["budget_limit"]) * 100
    remaining = max(0, tenant["budget_limit"] - total_spent)
    
    conn.close()
    
    return SpendingResponse(
        tenant_id=tenant["id"],
        tenant_name=tenant["name"],
        total_spent=total_spent,
        budget_limit=tenant["budget_limit"],
        percent_used=percent_used,
        remaining_budget=remaining
    )


@app.get("/tenants/{tenant_id}/costs", response_model=list[CostRecordResponse])
def get_tenant_costs(tenant_id: int, limit: int = 100):
    """Get cost history for a tenant."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM cost_records WHERE tenant_id = ? ORDER BY timestamp DESC LIMIT ?",
        (tenant_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    
    return [
        CostRecordResponse(
            id=row["id"],
            tenant_id=row["tenant_id"],
            model_name=row["model_name"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost=row["cost"],
            timestamp=row["timestamp"]
        )
        for row in rows
    ]


@app.get("/circuit-breaker/{tenant_id}", response_model=CircuitBreakerResponse)
def get_circuit_breaker(tenant_id: int):
    """Get circuit breaker state for a tenant."""
    if tenant_id not in circuit_breakers:
        return CircuitBreakerResponse(
            tenant_id=tenant_id,
            state=CircuitState.CLOSED,
            failure_count=0,
            last_failure=None
        )
    
    cb = circuit_breakers[tenant_id]
    return CircuitBreakerResponse(
        tenant_id=tenant_id,
        state=cb["state"],
        failure_count=cb["failure_count"],
        last_failure=cb.get("last_failure")
    )


@app.post("/circuit-breaker/{tenant_id}/reset")
def reset_circuit_breaker(tenant_id: int):
    """Manually reset circuit breaker for a tenant."""
    if tenant_id in circuit_breakers:
        circuit_breakers[tenant_id] = {"state": CircuitState.CLOSED, "failure_count": 0, "last_failure": None}
    return {"message": "Circuit breaker reset", "tenant_id": tenant_id}


@app.get("/alerts", response_model=list[dict])
def get_alerts(tenant_id: Optional[int] = None, limit: int = 50):
    """Get spending alerts."""
    conn = get_db()
    cursor = conn.cursor()
    
    if tenant_id:
        cursor.execute(
            "SELECT * FROM alerts WHERE tenant_id = ? ORDER BY triggered_at DESC LIMIT ?",
            (tenant_id, limit)
        )
    else:
        cursor.execute("SELECT * FROM alerts ORDER BY triggered_at DESC LIMIT ?", (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "alert_type": row["alert_type"],
            "message": row["message"],
            "threshold": row["threshold"],
            "current_value": row["current_value"],
            "triggered_at": row["triggered_at"]
        }
        for row in rows
    ]


@app.get("/dashboard", response_model=DashboardResponse)
def get_dashboard():
    """Get live cost dashboard with aggregate data."""
    conn = get_db()
    cursor = conn.cursor()
    
    # Total tenants
    cursor.execute("SELECT COUNT(*) as count FROM tenants")
    total_tenants = cursor.fetchone()["count"]
    
    # Total spending
    cursor.execute("SELECT SUM(cost) as total FROM cost_records")
    total_spending = cursor.fetchone()["total"] or 0
    
    # Active circuits (OPEN or HALF_OPEN)
    active_circuits = sum(
        1 for cb in circuit_breakers.values()
        if cb.get("state") in [CircuitState.OPEN, CircuitState.HALF_OPEN]
    )
    
    # Triggered alerts (last 24 hours)
    cursor.execute(
        "SELECT COUNT(*) as count FROM alerts WHERE triggered_at > datetime('now', '-1 day')"
    )
    triggered_alerts = cursor.fetchone()["count"]
    
    # Per-tenant spending
    cursor.execute("SELECT * FROM tenants")
    tenants = cursor.fetchall()
    
    tenant_spending = []
    for tenant in tenants:
        cursor.execute("SELECT SUM(cost) as total FROM cost_records WHERE tenant_id = ?", (tenant["id"],))
        spent = cursor.fetchone()["total"] or 0
        percent = (spent / tenant["budget_limit"]) * 100
        
        tenant_spending.append(SpendingResponse(
            tenant_id=tenant["id"],
            tenant_name=tenant["name"],
            total_spent=spent,
            budget_limit=tenant["budget_limit"],
            percent_used=percent,
            remaining_budget=max(0, tenant["budget_limit"] - spent)
        ))
    
    conn.close()
    
    return DashboardResponse(
        total_tenants=total_tenants,
        total_spending=total_spending,
        active_circuits=active_circuits,
        triggered_alerts=triggered_alerts,
        tenant_spending=tenant_spending
    )


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "LLM Cost Guardian"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
