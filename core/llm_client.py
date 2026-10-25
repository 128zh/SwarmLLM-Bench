"""
LLM API 客户端封装
==================
提供统一的 LLM API 调用接口，支持：

特性：
- 多提供商：Claude API / OpenAI API / 本地模型
- 异步非阻塞调用 (asyncio)
- 超时控制 + 自动重试 + 指数退避
- 语义缓存（相似态势复用决策结果）
- JSON Schema 结构化输出约束
- Token 用量统计与预算控制

使用示例：
    client = LLMClient(provider="claude", model="claude-haiku-4-5-20251001")
    result = await client.generate(
        system_prompt="你是无人机战术AI",
        user_prompt="当前态势: ...",
        output_schema={"type": "object", "properties": {...}},
    )
"""
import asyncio
import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from config import (
    LLM_PROVIDER, LLM_MODEL, LLM_MODEL_CRITICAL,
    LLM_API_KEY, LLM_BASE_URL,
    LLM_TIMEOUT_MS, LLM_MAX_RETRIES,
    LLM_CACHE_ENABLED, LLM_CACHE_SIZE,
    LLM_OUTPUT_MAX_TOKENS,
)


@dataclass
class LLMResponse:
    """LLM 调用响应"""
    content: str                        # 文本输出
    structured: Optional[dict] = None   # 结构化输出 (JSON Schema mode)
    model: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    error: Optional[str] = None


@dataclass
class LLMStats:
    """LLM 调用统计"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    cached_hits: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: float = 0.0
    budget_exceeded: int = 0

    @property
    def avg_latency_ms(self) -> float:
        if self.successful_calls == 0:
            return 0.0
        return self.total_latency_ms / self.successful_calls

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.successful_calls / self.total_calls


class SemanticCache:
    """
    语义缓存：相似态势 → 复用LLM决策

    策略：
    1. 计算输入的语义哈希（取关键字段）
    2. 相似度 > 阈值 → 直接返回缓存结果
    3. LRU 淘汰策略
    """

    def __init__(self, max_size: int = LLM_CACHE_SIZE):
        self._cache: OrderedDict = OrderedDict()
        self._max_size = max_size

    def _hash_key(self, system_prompt: str, user_prompt: str, schema_hash: str) -> str:
        """计算输入的语义哈希（取前200字符作为快速哈希）"""
        content = f"{system_prompt[:100]}|{user_prompt[:200]}|{schema_hash}"
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, system_prompt: str, user_prompt: str, schema_hash: str = "") -> Optional[LLMResponse]:
        """查找缓存"""
        key = self._hash_key(system_prompt, user_prompt, schema_hash)
        if key in self._cache:
            # LRU: 移到末尾
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, system_prompt: str, user_prompt: str, schema_hash: str, response: LLMResponse):
        """存入缓存"""
        key = self._hash_key(system_prompt, user_prompt, schema_hash)
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)  # LRU 淘汰
            self._cache[key] = response
            response.cached = False  # 原始响应标记为非缓存

    def size(self) -> int:
        return len(self._cache)

    def clear(self):
        self._cache.clear()


class LLMClient:
    """
    LLM API 客户端

    支持的提供商：
    - claude: Anthropic Claude API
    - openai: OpenAI API (GPT-4o-mini, etc.)
    - local: 本地模型 (需配置 LOCAL_LLM_URL)
    """

    def __init__(
        self,
        provider: str = LLM_PROVIDER,
        model: str = LLM_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_ms: int = LLM_TIMEOUT_MS,
        max_retries: int = LLM_MAX_RETRIES,
        cache_enabled: bool = LLM_CACHE_ENABLED,
    ):
        self.provider = provider
        self.model = model
        self.model_critical = LLM_MODEL_CRITICAL
        self.api_key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        self.base_url = base_url or LLM_BASE_URL
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries

        # 缓存
        self._cache = SemanticCache() if cache_enabled else None

        # 统计
        self.stats = LLMStats()

        # Token 预算
        self._token_budget_remaining = 1_000_000  # 默认100万token/天

    # ============ 公开接口 ============

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Optional[dict] = None,
        model: Optional[str] = None,
        max_tokens: int = LLM_OUTPUT_MAX_TOKENS,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """
        调用 LLM 生成响应

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户输入（战场态势描述）
            output_schema: JSON Schema 约束输出格式
            model: 模型覆盖
            max_tokens: 最大输出token
            temperature: 生成温度 (0-1, 战术决策建议用低温度)

        Returns:
            LLMResponse 对象
        """
        model = model or self.model
        schema_hash = hashlib.md5(
            json.dumps(output_schema, sort_keys=True).encode()
        ).hexdigest() if output_schema else ""

        # === 语义缓存查找 ===
        if self._cache:
            cached = self._cache.get(system_prompt, user_prompt, schema_hash)
            if cached:
                cached.cached = True
                self.stats.cached_hits += 1
                return cached

        # === Token 预算检查 ===
        estimated_input = len(system_prompt.split()) + len(user_prompt.split())
        if estimated_input > self._token_budget_remaining:
            self.stats.budget_exceeded += 1
            return LLMResponse(
                content="",
                error="Token budget exceeded — using FSM fallback",
                model=model,
            )

        # === 带重试的调用 ===
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                start = time.time()
                result = await self._call_api(
                    system_prompt, user_prompt, output_schema,
                    model, max_tokens, temperature,
                )
                latency = (time.time() - start) * 1000

                # 更新统计
                self.stats.total_calls += 1
                self.stats.successful_calls += 1
                self.stats.total_latency_ms += latency
                self.stats.total_input_tokens += result.input_tokens
                self.stats.total_output_tokens += result.output_tokens
                self._token_budget_remaining -= result.input_tokens

                result.latency_ms = latency
                result.model = model

                # 存入缓存
                if self._cache and output_schema:
                    self._cache.put(system_prompt, user_prompt, schema_hash, result)

                return result

            except asyncio.TimeoutError:
                last_error = f"Timeout after {self.timeout_ms}ms (attempt {attempt + 1})"
                if attempt < self.max_retries:
                    await asyncio.sleep(0.1 * (2 ** attempt))  # 指数退避
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    await asyncio.sleep(0.2 * (2 ** attempt))

        # 所有重试均失败
        self.stats.total_calls += 1
        self.stats.failed_calls += 1
        return LLMResponse(
            content="",
            error=last_error,
            model=model,
        )

    async def generate_critical(
        self, system_prompt: str, user_prompt: str,
        output_schema: Optional[dict] = None,
    ) -> LLMResponse:
        """关键决策调用（使用更强的模型）"""
        return await self.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
            model=self.model_critical,
            max_tokens=200,
            temperature=0.2,  # 更低温度，更确定性
        )

    # ============ 私有方法 ============

    async def _call_api(
        self, system_prompt: str, user_prompt: str,
        output_schema: Optional[dict], model: str,
        max_tokens: int, temperature: float,
    ) -> LLMResponse:
        """实际调用 LLM API"""
        if self.provider == "claude":
            return await self._call_claude(
                system_prompt, user_prompt, output_schema,
                model, max_tokens, temperature,
            )
        elif self.provider in ("openai", "local", "deepseek"):
            # DeepSeek / 本地模型使用 OpenAI 兼容 API
            return await self._call_openai(
                system_prompt, user_prompt, output_schema,
                model, max_tokens, temperature,
            )
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    async def _call_claude(
        self, system_prompt: str, user_prompt: str,
        output_schema: Optional[dict], model: str,
        max_tokens: int, temperature: float,
    ) -> LLMResponse:
        """调用 Anthropic Claude API"""
        import aiohttp

        if not self.api_key:
            raise ValueError("LLM_API_KEY or ANTHROPIC_API_KEY not set")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        messages = [{"role": "user", "content": user_prompt}]

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": messages,
        }

        # JSON Schema 约束 (tool_use 模式)
        if output_schema:
            body["tools"] = [{
                "name": "output_decision",
                "description": "Tactical decision output",
                "input_schema": output_schema,
            }]
            body["tool_choice"] = {"type": "tool", "name": "output_decision"}

        url = self.base_url or "https://api.anthropic.com/v1/messages"
        if not url.endswith("/messages"):
            url = url.rstrip("/") + "/messages"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_ms / 1000),
            ) as resp:
                data = await resp.json()

                if resp.status != 200:
                    raise RuntimeError(f"Claude API error {resp.status}: {data}")

                content_text = ""
                structured = None
                input_tokens = data.get("usage", {}).get("input_tokens", 0)
                output_tokens = data.get("usage", {}).get("output_tokens", 0)

                for block in data.get("content", []):
                    if block["type"] == "text":
                        content_text += block["text"]
                    elif block["type"] == "tool_use":
                        structured = block.get("input", {})

                return LLMResponse(
                    content=content_text,
                    structured=structured,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

    async def _call_openai(
        self, system_prompt: str, user_prompt: str,
        output_schema: Optional[dict], model: str,
        max_tokens: int, temperature: float,
    ) -> LLMResponse:
        """调用 OpenAI API"""
        import aiohttp

        if not self.api_key:
            raise ValueError("LLM_API_KEY not set")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        # JSON Schema 约束
        if output_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "tactical_decision",
                    "schema": output_schema,
                },
            }

        url = self.base_url or "https://api.openai.com/v1/chat/completions"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_ms / 1000),
            ) as resp:
                data = await resp.json()

                if resp.status != 200:
                    raise RuntimeError(f"OpenAI API error {resp.status}: {data}")

                content_text = data["choices"][0]["message"]["content"]
                input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
                output_tokens = data.get("usage", {}).get("completion_tokens", 0)

                structured = None
                if output_schema:
                    try:
                        structured = json.loads(content_text)
                    except json.JSONDecodeError:
                        pass

                return LLMResponse(
                    content=content_text,
                    structured=structured,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

    async def _call_local(
        self, system_prompt: str, user_prompt: str,
        output_schema: Optional[dict], model: str,
        max_tokens: int, temperature: float,
    ) -> LLMResponse:
        """调用本地模型 (兼容 OpenAI API 格式的本地服务)"""
        # 本地模型使用与 OpenAI 相同的 API 格式
        # 例如: Ollama, vLLM, LocalAI
        return await self._call_openai(
            system_prompt, user_prompt, output_schema,
            model, max_tokens, temperature,
        )

    # ============ 统计与监控 ============

    def get_stats(self) -> dict:
        """获取 LLM 调用统计"""
        return {
            "provider": self.provider,
            "model": self.model,
            "total_calls": self.stats.total_calls,
            "successful": self.stats.successful_calls,
            "failed": self.stats.failed_calls,
            "cached_hits": self.stats.cached_hits,
            "success_rate": round(self.stats.success_rate, 3),
            "avg_latency_ms": round(self.stats.avg_latency_ms, 1),
            "total_input_tokens": self.stats.total_input_tokens,
            "total_output_tokens": self.stats.total_output_tokens,
            "token_budget_remaining": self._token_budget_remaining,
            "cache_size": self._cache.size() if self._cache else 0,
        }

    def reset_stats(self):
        """重置统计"""
        self.stats = LLMStats()

    def set_token_budget(self, budget: int):
        """重置token预算"""
        self._token_budget_remaining = budget


# ============ 全局单例 ============
_llm_client: Optional[LLMClient] = None


def get_llm_client(
    provider: str = LLM_PROVIDER,
    model: str = LLM_MODEL,
    force_new: bool = False,
) -> LLMClient:
    """获取全局 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None or force_new:
        _llm_client = LLMClient(provider=provider, model=model)
    return _llm_client
