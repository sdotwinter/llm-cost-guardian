"""Configuration management for LLM Cost Guardian."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModelConfig:
    """Configuration for an LLM model."""
    
    name: str
    provider: str  # openai, anthropic, google
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    max_tokens: int = 4096


@dataclass
class LimitsConfig:
    """Spending and rate limits."""
    
    per_user_daily: float = 10.0
    per_model_daily: float = 50.0
    global_daily: float = 100.0
    per_request: float = 1.0
    requests_per_minute: int = 60


@dataclass
class AlertConfig:
    """Budget alert configuration."""
    
    threshold: int  # 50, 75, 90
    webhook_url: str = ""


@dataclass
class AutoRouteConfig:
    """Auto-routing configuration."""
    
    enabled: bool = False
    fallback_model: str = ""
    when_spend_exceeds: float = 0.75


@dataclass
class Config:
    """Main configuration for LLM Cost Guardian."""
    
    models: list[ModelConfig] = field(default_factory=list)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    alerts: list[AlertConfig] = field(default_factory=list)
    auto_route: AutoRouteConfig = field(default_factory=AutoRouteConfig)
    database_path: str = "~/.llm-cost-guardian/data.db"
    
    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        """Load configuration from YAML file."""
        path = Path(path).expanduser()
        if not path.exists():
            return cls()
        
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        
        models = []
        for m in data.get("models", []):
            models.append(ModelConfig(
                name=m["name"],
                provider=m["provider"],
                cost_per_1k_input=m.get("cost_per_1k_input", 0.0),
                cost_per_1k_output=m.get("cost_per_1k_output", 0.0),
                max_tokens=m.get("max_tokens", 4096),
            ))
        
        limits_data = data.get("limits", {})
        limits = LimitsConfig(
            per_user_daily=limits_data.get("per_user_daily", 10.0),
            per_model_daily=limits_data.get("per_model_daily", 50.0),
            global_daily=limits_data.get("global_daily", 100.0),
            per_request=limits_data.get("per_request", 1.0),
            requests_per_minute=limits_data.get("requests_per_minute", 60),
        )
        
        alerts = []
        for a in data.get("alerts", []):
            alerts.append(AlertConfig(
                threshold=a["threshold"],
                webhook_url=a.get("webhook_url", ""),
            ))
        
        auto_route_data = data.get("auto_route", {})
        auto_route = AutoRouteConfig(
            enabled=auto_route_data.get("enabled", False),
            fallback_model=auto_route_data.get("fallback_model", ""),
            when_spend_exceeds=auto_route_data.get("when_spend_exceeds", 0.75),
        )
        
        return cls(
            models=models,
            limits=limits,
            alerts=alerts,
            auto_route=auto_route,
            database_path=data.get("database_path", "~/.llm-cost-guardian/data.db"),
        )
    
    @classmethod
    def default_config_path(cls) -> Path:
        """Get default config path."""
        return Path.home() / ".llm-cost-guardian" / "config.yaml"
    
    def save(self, path: str | Path | None = None) -> None:
        """Save configuration to YAML file."""
        path = Path(path or self.default_config_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        
        data: dict[str, Any] = {
            "models": [
                {
                    "name": m.name,
                    "provider": m.provider,
                    "cost_per_1k_input": m.cost_per_1k_input,
                    "cost_per_1k_output": m.cost_per_1k_output,
                    "max_tokens": m.max_tokens,
                }
                for m in self.models
            ],
            "limits": {
                "per_user_daily": self.limits.per_user_daily,
                "per_model_daily": self.limits.per_model_daily,
                "global_daily": self.limits.global_daily,
                "per_request": self.limits.per_request,
                "requests_per_minute": self.limits.requests_per_minute,
            },
            "alerts": [
                {"threshold": a.threshold, "webhook_url": a.webhook_url}
                for a in self.alerts
            ],
            "auto_route": {
                "enabled": self.auto_route.enabled,
                "fallback_model": self.auto_route.fallback_model,
                "when_spend_exceeds": self.auto_route.when_spend_exceeds,
            },
            "database_path": self.database_path,
        }
        
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)
    
    def get_model(self, name: str) -> ModelConfig | None:
        """Get model config by name."""
        for model in self.models:
            if model.name == name:
                return model
        return None
    
    @classmethod
    def create_default(cls) -> "Config":
        """Create default configuration with common models."""
        models = [
            ModelConfig(
                name="gpt-4o",
                provider="openai",
                cost_per_1k_input=0.0025,
                cost_per_1k_output=0.01,
                max_tokens=128000,
            ),
            ModelConfig(
                name="gpt-4o-mini",
                provider="openai",
                cost_per_1k_input=0.00015,
                cost_per_1k_output=0.0006,
                max_tokens=128000,
            ),
            ModelConfig(
                name="gpt-4-turbo",
                provider="openai",
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
                max_tokens=128000,
            ),
            ModelConfig(
                name="claude-3-5-sonnet",
                provider="anthropic",
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                max_tokens=200000,
            ),
            ModelConfig(
                name="claude-3-opus",
                provider="anthropic",
                cost_per_1k_input=0.015,
                cost_per_1k_output=0.075,
                max_tokens=200000,
            ),
            ModelConfig(
                name="claude-3-haiku",
                provider="anthropic",
                cost_per_1k_input=0.00025,
                cost_per_1k_output=0.00125,
                max_tokens=200000,
            ),
            ModelConfig(
                name="gemini-1.5-pro",
                provider="google",
                cost_per_1k_input=0.00125,
                cost_per_1k_output=0.005,
                max_tokens=128000,
            ),
            ModelConfig(
                name="gemini-1.5-flash",
                provider="google",
                cost_per_1k_input=0.000075,
                cost_per_1k_output=0.0003,
                max_tokens=128000,
            ),
        ]
        
        return cls(
            models=models,
            limits=LimitsConfig(),
            alerts=[
                AlertConfig(threshold=50),
                AlertConfig(threshold=75),
                AlertConfig(threshold=90),
            ],
            auto_route=AutoRouteConfig(
                enabled=True,
                fallback_model="gpt-4o-mini",
                when_spend_exceeds=0.75,
            ),
        )
