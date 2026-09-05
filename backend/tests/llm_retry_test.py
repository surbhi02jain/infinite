"""Unit tests: LLM timeout retry before deterministic fallback."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx

import orchestrator
from orchestrator import Orchestrator


class _EmitOnly(Orchestrator):
    def __init__(self):
        self.messages = []

    async def emit(self, run_id, stage, agent, level, etype, message, data=None):
        self.messages.append(message)
        return {}


def test_read_timeout_is_retryable():
    fn = getattr(orchestrator, "_is_retryable_llm_error", None)
    assert fn is not None
    assert fn(httpx.ReadTimeout("timed out"))
    assert fn(TimeoutError())
    assert not fn(RuntimeError("Sarvam API error 402: No credits available."))


def test_safe_llm_retries_once_on_read_timeout_then_uses_llm_result():
    orch = _EmitOnly()
    calls = {"n": 0}

    async def flaky(system, prompt, model, session=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("timed out")
        return {"flows": [{"name": "from-llm"}]}, '{"flows":[{"name":"from-llm"}]}'

    async def run():
        with patch.object(orchestrator, "llm_json", flaky), \
             patch.object(orchestrator, "LLM_RETRY_BACKOFF", 0, create=True):
            return await orch._safe_llm("sys", "prompt", "sarvam-105b", "run-1", "PLAN", "planner")

    data, text = asyncio.run(run())
    assert calls["n"] == 2
    assert data == {"flows": [{"name": "from-llm"}]}
    assert any("retrying" in m.lower() for m in orch.messages)
    assert not any("deterministic fallback" in m for m in orch.messages)


def test_safe_llm_falls_back_after_retries_exhausted():
    orch = _EmitOnly()
    flaky = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))

    async def run():
        with patch.object(orchestrator, "llm_json", flaky), \
             patch.object(orchestrator, "LLM_RETRY_BACKOFF", 0, create=True):
            return await orch._safe_llm("sys", "prompt", "sarvam-105b", "run-1", "PLAN", "planner")

    data, text = asyncio.run(run())
    assert flaky.await_count == getattr(orchestrator, "LLM_ATTEMPTS", 2)
    assert data is None
    assert text == ""
    assert any("deterministic fallback" in m for m in orch.messages)


def test_safe_llm_does_not_retry_non_retryable_errors():
    orch = _EmitOnly()
    boom = AsyncMock(side_effect=RuntimeError("Sarvam API error 402: No credits available."))

    async def run():
        with patch.object(orchestrator, "llm_json", boom), \
             patch.object(orchestrator, "LLM_RETRY_BACKOFF", 0, create=True):
            return await orch._safe_llm("sys", "prompt", "sarvam-105b", "run-1", "PLAN", "planner")

    data, _ = asyncio.run(run())
    assert boom.await_count == 1
    assert data is None
    assert any("deterministic fallback" in m for m in orch.messages)


def test_llm_timeouts_are_longer_than_the_old_55s_budget():
    assert getattr(orchestrator, "LLM_HTTP_TIMEOUT", 55) >= 120
    assert getattr(orchestrator, "LLM_CALL_TIMEOUT", 75) > getattr(orchestrator, "LLM_HTTP_TIMEOUT", 55)
    assert getattr(orchestrator, "LLM_ATTEMPTS", 1) >= 2
