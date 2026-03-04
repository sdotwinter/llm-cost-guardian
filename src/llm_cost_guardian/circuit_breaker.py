"""Circuit breaker module for LLM Cost Guardian."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from llm_cost_guardian.config import Config, LimitsConfig


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocked - over limits
    HALF_OPEN = "half_open"  # Testing if limits reset


@dataclass
class CircuitBreakerResult:
    """Result of circuit breaker check."""
    allowed: bool
    reason: str | None
    state: CircuitState
    current_spend: float
    limit: float
    percent_used: float


class CircuitBreaker:
    """Circuit breaker for LLM cost control."""
    
    def __init__(self, config: Config, database: "llm_cost_guardian.database.CostDatabase"):
        from llm_cost_guardian import database as db_module
        self.database: db_module.CostDatabase = database
        self.config = config
        self.limits = config.limits
    
    async def check_request(
        self,
        user_id: str,
        model: str,
        estimated_cost: float,
    ) -> CircuitBreakerResult:
        """
        Check if a request should be allowed.
        
        Returns CircuitBreakerResult with:
        - allowed: Whether the request can proceed
        - reason: Why request was blocked (if blocked)
        - state: Current circuit state
        - current_spend: Current spending
        - limit: The limit that was hit (if any)
        - percent_used: Percentage of limit used
        """
        # Check per-user daily limit
        user_spend = await self.database.get_user_daily_spend(user_id)
        user_limit = self.limits.per_user_daily
        user_percent = (user_spend / user_limit * 100) if user_limit > 0 else 0
        
        if user_spend + estimated_cost > user_limit:
            return CircuitBreakerResult(
                allowed=False,
                reason=f"User daily limit exceeded: ${user_spend:.2f}/${user_limit:.2f}",
                state=CircuitState.OPEN,
                current_spend=user_spend,
                limit=user_limit,
                percent_used=user_percent,
            )
        
        # Check per-model daily limit
        model_spend = await self.database.get_model_daily_spend(model)
        model_limit = self.limits.per_model_daily
        model_percent = (model_spend / model_limit * 100) if model_limit > 0 else 0
        
        if model_spend + estimated_cost > model_limit:
            return CircuitBreakerResult(
                allowed=False,
                reason=f"Model daily limit exceeded: ${model_spend:.2f}/${model_limit:.2f}",
                state=CircuitState.OPEN,
                current_spend=model_spend,
                limit=model_limit,
                percent_used=model_percent,
            )
        
        # Check global daily limit
        global_spend = await self.database.get_global_daily_spend()
        global_limit = self.limits.global_daily
        global_percent = (global_spend / global_limit * 100) if global_limit > 0 else 0
        
        if global_spend + estimated_cost > global_limit:
            return CircuitBreakerResult(
                allowed=False,
                reason=f"Global daily limit exceeded: ${global_spend:.2f}/${global_limit:.2f}",
                state=CircuitState.OPEN,
                current_spend=global_spend,
                limit=global_limit,
                percent_used=global_percent,
            )
        
        # Check per-request limit
        if estimated_cost > self.limits.per_request:
            return CircuitBreakerResult(
                allowed=False,
                reason=f"Per-request limit exceeded: ${estimated_cost:.2f}/${self.limits.per_request:.2f}",
                state=CircuitState.OPEN,
                current_spend=estimated_cost,
                limit=self.limits.per_request,
                percent_used=100.0,
            )
        
        # Check rate limit (requests per minute)
        request_count = await self.database.get_user_request_count(user_id)
        if request_count >= self.limits.requests_per_minute:
            return CircuitBreakerResult(
                allowed=False,
                reason=f"Rate limit exceeded: {request_count}/{self.limits.requests_per_minute} requests per minute",
                state=CircuitState.OPEN,
                current_spend=user_spend,
                limit=float(self.limits.requests_per_minute),
                percent_used=100.0,
            )
        
        # All checks passed - determine state based on how close to limits we are
        max_percent = max(user_percent, model_percent, global_percent)
        if max_percent >= 90:
            state = CircuitState.HALF_OPEN
            reason = "Close to limits (90%+)"
        else:
            state = CircuitState.CLOSED
            reason = None
        
        return CircuitBreakerResult(
            allowed=True,
            reason=reason,
            state=state,
            current_spend=user_spend,
            limit=user_limit,
            percent_used=user_percent,
        )
    
    async def should_auto_route(self, user_id: str, model: str) -> bool:
        """Check if auto-routing should trigger."""
        if not self.config.auto_route.enabled:
            return False
        
        user_spend = await self.database.get_user_daily_spend(user_id)
        user_limit = self.limits.per_user_daily
        threshold = self.config.auto_route.when_spend_exceeds
        
        return (user_spend / user_limit) >= threshold if user_limit > 0 else False
    
    def get_fallback_model(self) -> str:
        """Get the configured fallback model."""
        return self.config.auto_route.fallback_model


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    
    def __init__(self, result: CircuitBreakerResult):
        self.result = result
        super().__init__(result.reason)
