"""Tests for LLM Cost Guardian."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_cost_guardian import circuit_breaker, config, database


class TestConfig:
    """Tests for configuration module."""
    
    def test_create_default_config(self):
        """Test creating default configuration."""
        cfg = config.Config.create_default()
        
        assert len(cfg.models) > 0
        assert cfg.limits.per_user_daily == 10.0
        assert cfg.limits.per_model_daily == 50.0
        assert cfg.limits.global_daily == 100.0
        assert cfg.auto_route.enabled is True
    
    def test_get_model_by_name(self):
        """Test getting model config by name."""
        cfg = config.Config.create_default()
        
        gpt4 = cfg.get_model("gpt-4o")
        assert gpt4 is not None
        assert gpt4.provider == "openai"
        assert gpt4.cost_per_1k_input == 0.0025
        
        unknown = cfg.get_model("unknown-model")
        assert unknown is None
    
    def test_config_save_load(self, tmp_path):
        """Test saving and loading config."""
        cfg = config.Config.create_default()
        cfg.limits.per_user_daily = 25.0
        
        config_path = tmp_path / "config.yaml"
        cfg.save(config_path)
        
        loaded = config.Config.from_file(config_path)
        assert loaded.limits.per_user_daily == 25.0
        assert len(loaded.models) > 0


class TestCostDatabase:
    """Tests for cost database."""
    
    @pytest.fixture
    def db_path(self, tmp_path):
        """Create temporary database path."""
        return tmp_path / "test.db"
    
    @pytest.mark.asyncio
    async def test_init_database(self, db_path):
        """Test database initialization."""
        db = database.CostDatabase(db_path)
        await db.init()
        
        assert db_path.exists()
        
        await db.close()
    
    @pytest.mark.asyncio
    async def test_record_cost(self, db_path):
        """Test recording a cost."""
        db = database.CostDatabase(db_path)
        await db.init()
        
        record = await db.record_cost(
            user_id="user_123",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            cost=0.001,
            prompt="Hello world",
        )
        
        assert record.user_id == "user_123"
        assert record.model == "gpt-4o"
        assert record.cost == 0.001
        
        await db.close()
    
    @pytest.mark.asyncio
    async def test_get_user_daily_spend(self, db_path):
        """Test getting user daily spend."""
        db = database.CostDatabase(db_path)
        await db.init()
        
        # Record some costs
        await db.record_cost("user_1", "gpt-4o", 100, 50, 0.001, "test")
        await db.record_cost("user_1", "gpt-4o", 100, 50, 0.002, "test")
        await db.record_cost("user_2", "gpt-4o", 100, 50, 0.003, "test")
        
        spend = await db.get_user_daily_spend("user_1")
        assert spend == 0.003
        
        await db.close()
    
    @pytest.mark.asyncio
    async def test_get_model_daily_spend(self, db_path):
        """Test getting model daily spend."""
        db = database.CostDatabase(db_path)
        await db.init()
        
        await db.record_cost("user_1", "gpt-4o", 100, 50, 0.001, "test")
        await db.record_cost("user_2", "gpt-4o", 100, 50, 0.002, "test")
        await db.record_cost("user_1", "gpt-4o-mini", 100, 50, 0.0005, "test")
        
        gpt4_spend = await db.get_model_daily_spend("gpt-4o")
        mini_spend = await db.get_model_daily_spend("gpt-4o-mini")
        
        assert gpt4_spend == 0.003
        assert mini_spend == 0.0005
        
        await db.close()
    
    @pytest.mark.asyncio
    async def test_get_global_daily_spend(self, db_path):
        """Test getting global daily spend."""
        db = database.CostDatabase(db_path)
        await db.init()
        
        await db.record_cost("user_1", "gpt-4o", 100, 50, 0.001, "test")
        await db.record_cost("user_2", "gpt-4o", 100, 50, 0.002, "test")
        
        spend = await db.get_global_daily_spend()
        assert spend == 0.003
        
        await db.close()
    
    @pytest.mark.asyncio
    async def test_get_top_users(self, db_path):
        """Test getting top users."""
        db = database.CostDatabase(db_path)
        await db.init()
        
        await db.record_cost("user_1", "gpt-4o", 100, 50, 0.001, "test")
        await db.record_cost("user_2", "gpt-4o", 100, 50, 0.005, "test")
        await db.record_cost("user_2", "gpt-4o", 100, 50, 0.003, "test")
        
        top = await db.get_top_users(2)
        
        assert len(top) == 2
        assert top[0]["user_id"] == "user_2"
        assert top[0]["total_cost"] == 0.008
        
        await db.close()


class TestCircuitBreaker:
    """Tests for circuit breaker."""
    
    @pytest.fixture
    async def db(self, tmp_path):
        """Create test database."""
        db = database.CostDatabase(tmp_path / "test.db")
        await db.init()
        yield db
        await db.close()
    
    @pytest.fixture
    def cfg(self):
        """Create test config."""
        cfg = config.Config.create_default()
        cfg.limits.per_user_daily = 1.0
        cfg.limits.per_model_daily = 5.0
        cfg.limits.global_daily = 10.0
        cfg.limits.per_request = 0.5
        cfg.limits.requests_per_minute = 10
        return cfg
    
    @pytest.mark.asyncio
    async def test_allows_request_under_limit(self, db, cfg):
        """Test that requests under limits are allowed."""
        cb = circuit_breaker.CircuitBreaker(cfg, db)
        
        result = await cb.check_request("user_1", "gpt-4o", 0.01)
        
        assert result.allowed is True
        assert result.state == circuit_breaker.CircuitState.CLOSED
    
    @pytest.mark.asyncio
    async def test_blocks_user_daily_limit(self, db, cfg):
        """Test that user daily limit blocks requests."""
        cb = circuit_breaker.CircuitBreaker(cfg, db)
        
        # Record costs up to limit
        await db.record_cost("user_1", "gpt-4o", 100, 100, 0.5, "test")
        await db.record_cost("user_1", "gpt-4o", 100, 100, 0.5, "test")
        
        result = await cb.check_request("user_1", "gpt-4o", 0.01)
        
        assert result.allowed is False
        assert "User daily limit" in result.reason
    
    @pytest.mark.asyncio
    async def test_blocks_per_request_limit(self, db, cfg):
        """Test that per-request limit blocks requests."""
        cb = circuit_breaker.CircuitBreaker(cfg, db)
        
        result = await cb.check_request("user_1", "gpt-4o", 1.0)
        
        assert result.allowed is False
        assert "Per-request limit" in result.reason
    
    @pytest.mark.asyncio
    async def test_rate_limiting(self, db, cfg):
        """Test rate limiting."""
        cb = circuit_breaker.CircuitBreaker(cfg, db)
        
        # Record 10 requests (at limit)
        for i in range(10):
            await db.record_cost("user_1", "gpt-4o", 10, 10, 0.001, f"test{i}")
        
        # Next request should be blocked
        result = await cb.check_request("user_1", "gpt-4o", 0.001)
        
        assert result.allowed is False
        assert "Rate limit" in result.reason
    
    @pytest.mark.asyncio
    async def test_auto_route_threshold(self, db, cfg):
        """Test auto-routing threshold."""
        cfg.auto_route.enabled = True
        cfg.auto_route.when_spend_exceeds = 0.5
        cfg.auto_route.fallback_model = "gpt-4o-mini"
        
        cb = circuit_breaker.CircuitBreaker(cfg, db)
        
        # Spend is 0, limit is 1.0, so 0% - no route
        should_route = await cb.should_auto_route("user_1", "gpt-4o")
        assert should_route is False
        
        # Add more spend to cross threshold
        await db.record_cost("user_1", "gpt-4o", 1000, 1000, 0.6, "test")
        
        should_route = await cb.should_auto_route("user_1", "gpt-4o")
        assert should_route is True
        
        fallback = cb.get_fallback_model()
        assert fallback == "gpt-4o-mini"


class TestConfigIntegration:
    """Integration tests for config."""
    
    def test_default_models_have_correct_providers(self):
        """Test default models have correct provider mapping."""
        cfg = config.Config.create_default()
        
        providers = {m.name: m.provider for m in cfg.models}
        
        assert providers["gpt-4o"] == "openai"
        assert providers["claude-3-5-sonnet"] == "anthropic"
        assert providers["gemini-1.5-pro"] == "google"
    
    def test_default_models_have_pricing(self):
        """Test default models have pricing."""
        cfg = config.Config.create_default()
        
        for model in cfg.models:
            assert model.cost_per_1k_input > 0
            assert model.cost_per_1k_output > 0
