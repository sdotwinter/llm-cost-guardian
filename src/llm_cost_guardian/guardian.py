"""Main LLM Cost Guardian module."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from llm_cost_guardian import circuit_breaker, config, database


@dataclass
class CallResult:
    """Result of an LLM call."""
    success: bool
    response: Any | None
    error: str | None
    cost: float
    prompt_tokens: int
    completion_tokens: int
    model: str
    circuit_state: circuit_breaker.CircuitState


class LLMCostGuardian:
    """Main class for LLM Cost Guardian."""
    
    def __init__(
        self,
        config_path: str | Path | None = None,
        api_key: str | None = None,
    ):
        self.config_path = config_path
        if config_path:
            self.config = config.Config.from_file(config_path)
        else:
            self.config = config.Config.create_default()
        
        self.db = database.CostDatabase(self.config.database_path)
        self.circuit = circuit_breaker.CircuitBreaker(self.config, self.db)
        self.api_key = api_key
        
        self._client: httpx.AsyncClient | None = None
    
    async def __aenter__(self) -> "LLMCostGuardian":
        await self.init()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
    
    async def init(self) -> None:
        """Initialize the guardian."""
        await self.db.init()
        self._client = httpx.AsyncClient(timeout=60.0)
    
    async def close(self) -> None:
        """Close connections."""
        if self._client:
            await self._client.aclose()
        await self.db.close()
    
    def calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Calculate cost for a request."""
        model_config = self.config.get_model(model)
        if not model_config:
            return 0.0
        
        input_cost = (prompt_tokens / 1000) * model_config.cost_per_1k_input
        output_cost = (completion_tokens / 1000) * model_config.cost_per_1k_output
        return input_cost + output_cost
    
    def estimate_cost(
        self,
        model: str,
        estimated_prompt_tokens: int,
        estimated_completion_tokens: int,
    ) -> float:
        """Estimate cost before making a request."""
        return self.calculate_cost(model, estimated_prompt_tokens, estimated_completion_tokens)
    
    async def call(
        self,
        model: str,
        messages: list[dict[str, str]],
        user_id: str = "default",
        temperature: float = 1.0,
        max_tokens: int | None = None,
    ) -> CallResult:
        """Make an LLM API call through the guardian."""
        prompt = "\n".join(m.get("content", "") for m in messages)
        
        # Estimate cost (rough estimate based on prompt length)
        estimated_prompt_tokens = len(prompt) // 4  # rough approximation
        estimated_completion_tokens = max_tokens or 500
        estimated_cost = self.estimate_cost(model, estimated_prompt_tokens, estimated_completion_tokens)
        
        # Check circuit breaker
        check_result = await self.circuit.check_request(user_id, model, estimated_cost)
        
        if not check_result.allowed:
            return CallResult(
                success=False,
                response=None,
                error=check_result.reason,
                cost=0.0,
                prompt_tokens=0,
                completion_tokens=0,
                model=model,
                circuit_state=check_result.state,
            )
        
        # Make the actual API call based on provider
        model_config = self.config.get_model(model)
        provider = model_config.provider if model_config else "openai"
        
        try:
            if provider == "openai":
                response = await self._call_openai(model, messages, temperature, max_tokens)
            elif provider == "anthropic":
                response = await self._call_anthropic(model, messages, temperature, max_tokens)
            elif provider == "google":
                response = await self._call_google(model, messages, temperature, max_tokens)
            else:
                return CallResult(
                    success=False,
                    response=None,
                    error=f"Unknown provider: {provider}",
                    cost=0.0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    model=model,
                    circuit_state=circuit_breaker.CircuitState.CLOSED,
                )
            
            # Extract usage and calculate actual cost
            prompt_tokens = response.get("usage", {}).get("prompt_tokens", 0)
            completion_tokens = response.get("usage", {}).get("completion_tokens", 0)
            cost = self.calculate_cost(model, prompt_tokens, completion_tokens)
            
            # Record the cost
            await self.db.record_cost(
                user_id=user_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                prompt=prompt,
                request_id=response.get("id"),
            )
            
            return CallResult(
                success=True,
                response=response,
                error=None,
                cost=cost,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=model,
                circuit_state=check_result.state,
            )
            
        except Exception as e:
            return CallResult(
                success=False,
                response=None,
                error=str(e),
                cost=0.0,
                prompt_tokens=0,
                completion_tokens=0,
                model=model,
                circuit_state=circuit_breaker.CircuitState.CLOSED,
            )
    
    async def _call_openai(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Call OpenAI API."""
        api_key = self.api_key or self._get_env_api_key("OPENAI_API_KEY")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        response = await self._client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()
    
    async def _call_anthropic(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Call Anthropic API."""
        api_key = self.api_key or self._get_env_api_key("ANTHROPIC_API_KEY")
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        
        # Convert messages to Anthropic format
        system = ""
        anthropic_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
            else:
                anthropic_messages.append(msg)
        
        payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 1024,
        }
        if system:
            payload["system"] = system
        
        response = await self._client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        
        # Convert to OpenAI-style response for consistency
        return {
            "id": data.get("id"),
            "choices": [{"message": {"content": data.get("content", [{}])[0].get("text", "")}}],
            "usage": {
                "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
            },
        }
    
    async def _call_google(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Call Google Gemini API."""
        api_key = self._get_env_api_key("GOOGLE_API_KEY")
        
        # Convert messages to Google format
        contents = []
        for msg in messages:
            contents.append({
                "role": msg.get("role", "user"),
                "parts": [{"text": msg.get("content", "")}],
            })
        
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        
        response = await self._client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        
        # Convert to OpenAI-style response for consistency
        content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return {
            "id": data.get("promptFeedback", {}).get("serviceMetadata", {}).get("modelId", model),
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": data.get("usageMetadata", {}).get("promptTokenCount", 0),
                "completion_tokens": data.get("usageMetadata", {}).get("candidatesTokenCount", 0),
            },
        }
    
    def _get_env_api_key(self, key_name: str) -> str:
        """Get API key from environment."""
        import os
        api_key = os.environ.get(key_name)
        if not api_key:
            raise ValueError(f"API key not found: {key_name}. Set it as an environment variable or pass it to the constructor.")
        return api_key
    
    # Synchronous convenience methods
    
    async def get_status(self) -> dict[str, Any]:
        """Get current spending status."""
        return {
            "user_spend": {},
            "model_spend": {},
            "global_spend": await self.db.get_global_daily_spend(),
            "top_users": await self.db.get_top_users(),
            "top_models": await self.db.get_top_models(),
        }
    
    async def get_user_status(self, user_id: str) -> dict[str, Any]:
        """Get status for a specific user."""
        user_spend = await self.db.get_user_daily_spend(user_id)
        user_requests = await self.db.get_user_request_count(user_id)
        
        return {
            "user_id": user_id,
            "daily_spend": user_spend,
            "daily_limit": self.config.limits.per_user_daily,
            "percent_used": (user_spend / self.config.limits.per_user_daily * 100) if self.config.limits.per_user_daily > 0 else 0,
            "requests_this_minute": user_requests,
            "rate_limit": self.config.limits.requests_per_minute,
        }
