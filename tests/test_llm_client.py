"""Tests for OpenAI-compatible LLM client helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx
import pytest

import paper_engine.agent.llm_client as llm_client


SIMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
    },
    "required": ["answer"],
    "additionalProperties": False,
}


def make_chat_response(
    content: str | None,
    *,
    status_code: int = 200,
    refusal: str | None = None,
) -> httpx.Response:
    message: dict[str, Any] = {"content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return httpx.Response(
        status_code,
        json={"choices": [{"message": message}]},
        request=httpx.Request("POST", "http://llm.example/chat/completions"),
    )


def install_fake_llm(
    monkeypatch: pytest.MonkeyPatch,
    responses: Iterable[httpx.Response],
    *,
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "test-key",
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    queued_responses = list(responses)

    async def fake_config() -> dict[str, str]:
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": "gpt-4o",
        }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> httpx.Response:
            calls.append({"url": url, "headers": headers, "json": json})
            if not queued_responses:
                raise AssertionError("unexpected LLM request")
            return queued_responses.pop(0)

    monkeypatch.setattr(llm_client, "get_llm_config", fake_config)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", FakeAsyncClient)
    return calls


@pytest.mark.asyncio
async def test_call_llm_schema_uses_strict_json_schema_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_llm(
        monkeypatch,
        [make_chat_response('{"answer": "yes"}')],
    )

    result = await llm_client.call_llm_schema(
        "system",
        "user",
        "answer_schema",
        SIMPLE_SCHEMA,
    )

    assert result == {"answer": "yes"}
    response_format = calls[0]["json"]["response_format"]
    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "answer_schema",
            "strict": True,
            "schema": SIMPLE_SCHEMA,
        },
    }


@pytest.mark.asyncio
async def test_call_llm_schema_uses_json_mode_when_provider_lacks_schema_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_llm(
        monkeypatch,
        [make_chat_response('{"answer": "local"}')],
        base_url="http://localhost:11434/v1",
        api_key="",
    )

    result = await llm_client.call_llm_schema(
        "system",
        "user",
        "answer_schema",
        SIMPLE_SCHEMA,
        provider_capabilities={"json_schema": False},
    )

    assert result == {"answer": "local"}
    assert len(calls) == 1
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}
    user_prompt = calls[0]["json"]["messages"][1]["content"]
    assert "JSON mode schema contract" in user_prompt
    assert "top-level JSON value must be an object" in user_prompt
    assert '"required":["answer"]' in user_prompt


@pytest.mark.asyncio
async def test_call_llm_schema_omits_authorization_header_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_llm(
        monkeypatch,
        [make_chat_response('{"answer": "local"}')],
        base_url="http://127.0.0.1:8766/v1",
        api_key="",
    )

    result = await llm_client.call_llm_schema(
        "system",
        "user",
        "answer_schema",
        SIMPLE_SCHEMA,
    )

    assert result == {"answer": "local"}
    assert "Authorization" not in calls[0]["headers"]


@pytest.mark.asyncio
async def test_call_llm_schema_falls_back_to_json_mode_when_local_schema_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_llm(
        monkeypatch,
        [
            httpx.Response(
                400,
                json={"error": {"message": "unsupported response_format json_schema"}},
                request=httpx.Request(
                    "POST", "http://localhost:11434/v1/chat/completions"
                ),
            ),
            make_chat_response('{"answer": "fallback"}'),
        ],
        base_url="http://localhost:11434/v1",
        api_key="",
    )

    result = await llm_client.call_llm_schema(
        "system",
        "user",
        "answer_schema",
        SIMPLE_SCHEMA,
    )

    assert result == {"answer": "fallback"}
    assert len(calls) == 2
    assert calls[0]["json"]["response_format"]["type"] == "json_schema"
    assert calls[1]["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_call_llm_schema_retries_invalid_json_and_schema_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_llm(
        monkeypatch,
        [
            make_chat_response("not json"),
            make_chat_response('{"wrong": "shape"}'),
            make_chat_response('{"answer": "ok"}'),
        ],
    )

    result = await llm_client.call_llm_schema(
        "system",
        "user",
        "answer_schema",
        SIMPLE_SCHEMA,
    )

    assert result == {"answer": "ok"}
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_call_llm_schema_retries_model_refusals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_fake_llm(
        monkeypatch,
        [
            make_chat_response(None, refusal="I cannot comply."),
            make_chat_response('{"answer": "after retry"}'),
        ],
    )

    result = await llm_client.call_llm_schema(
        "system",
        "user",
        "answer_schema",
        SIMPLE_SCHEMA,
    )

    assert result == {"answer": "after retry"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_call_llm_schema_raises_after_retryable_failures_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_llm(
        monkeypatch,
        [
            make_chat_response('{"wrong": 1}'),
            make_chat_response('{"wrong": 2}'),
            make_chat_response('{"wrong": 3}'),
        ],
    )

    with pytest.raises(llm_client.LLMStructuredOutputError, match="schema"):
        await llm_client.call_llm_schema(
            "system",
            "user",
            "answer_schema",
            SIMPLE_SCHEMA,
        )


@pytest.mark.asyncio
async def test_call_llm_schema_wraps_timeout_with_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_config() -> dict[str, str]:
        return {
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "timeout_seconds": "7",
        }

    class TimeoutAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "TimeoutAsyncClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> httpx.Response:
            request = httpx.Request("POST", url)
            raise httpx.ReadTimeout("read timed out", request=request)

    monkeypatch.setattr(llm_client, "get_llm_config", fake_config)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", TimeoutAsyncClient)

    with pytest.raises(llm_client.LLMRequestError) as exc_info:
        await llm_client.call_llm_schema(
            "system",
            "user",
            "answer_schema",
            SIMPLE_SCHEMA,
        )

    message = str(exc_info.value)
    assert "LLM request timed out after 7s" in message
    assert "after 3 attempt(s)" in message
    assert "base_url=https://api.deepseek.com" in message
    assert "model=deepseek-v4-flash" in message


@pytest.mark.asyncio
async def test_call_llm_schema_retries_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_config() -> dict[str, str]:
        return {
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "timeout_seconds": "60",
        }

    attempts = 0

    class FlakyAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FlakyAsyncClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            request = httpx.Request("POST", url)
            if attempts == 1:
                raise httpx.ConnectTimeout("connect timed out", request=request)
            return make_chat_response('{"answer": "ok"}')

    monkeypatch.setattr(llm_client, "get_llm_config", fake_config)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", FlakyAsyncClient)
    monkeypatch.setattr(llm_client.asyncio, "sleep", lambda delay: _noop_sleep())

    result = await llm_client.call_llm_schema(
        "system",
        "user",
        "answer_schema",
        SIMPLE_SCHEMA,
    )

    assert result == {"answer": "ok"}
    assert attempts == 2


async def _noop_sleep() -> None:
    return None
