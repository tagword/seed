from __future__ import annotations

from seed.core import llm_exec


def test_minimax_input_builder_always_returns_tuple() -> None:
    items, tools = llm_exec._to_minimax_responses_input(  # type: ignore[attr-defined]
        [{"role": "user", "content": "hello"}],
        tools=None,
    )
    assert isinstance(items, list)
    assert tools == []


def test_generate_preserves_zero_temperature(monkeypatch) -> None:
    captured = {}

    class _Resp:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            }

    def _fake_post(url, headers, json, timeout, proxies):  # noqa: ANN001
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(llm_exec.requests, "post", _fake_post)

    exe = llm_exec.LLMAPIExecutor(baseURL="https://api.openai.com/v1", model="gpt-test")
    content, _meta = exe.generate([{"role": "user", "content": "hello"}], temperature=0)

    assert content == "ok"
    assert captured["json"]["temperature"] == 0


def test_generate_stream_preserves_zero_temperature(monkeypatch) -> None:
    captured = {}

    class _Resp:
        ok = True
        status_code = 200
        text = ""
        encoding = "utf-8"

        def iter_lines(self, decode_unicode=True):  # noqa: ANN001
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"

        def close(self):
            return None

    def _fake_post(url, headers, json, stream, timeout, proxies):  # noqa: ANN001
        captured["json"] = json
        assert stream is True
        return _Resp()

    monkeypatch.setattr(llm_exec.requests, "post", _fake_post)

    exe = llm_exec.LLMAPIExecutor(baseURL="https://api.openai.com/v1", model="gpt-test")
    chunks = list(exe.generate_stream([{"role": "user", "content": "hello"}], temperature=0))

    assert captured["json"]["temperature"] == 0
    assert any(c.get("type") == "done" for c in chunks)


# ---------------------------------------------------------------------------
# MiniMax thinking-mode wire payload (regression: thinking/reasoning_split
# used to be dropped or mis-placed, so "关闭思考" silently did nothing)
# ---------------------------------------------------------------------------

def _capture_minimax_payload(model, *, thinking, effort=None):
    import json
    from unittest.mock import MagicMock, patch
    import seed.core.llm_exec as llm_exec

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, proxies=None):
        r = MagicMock()
        r.ok = True
        r.status_code = 200
        if "input_tokens" in str(url):
            r.json.return_value = {"input_tokens": 12}
            return r
        captured.clear()
        captured.update(json or {})
        r.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "2"}}],
            "usage": {},
        }
        return r

    ex = llm_exec.LLMAPIExecutor(
        baseURL="https://api.minimaxi.com/v1", model=model, api_key="sk-test", maxOutputTokens=4096
    )
    with patch.object(llm_exec.requests, "post", side_effect=fake_post):
        ex.generate(
            messages=[{"role": "user", "content": "hi"}],
            enable_thinking=thinking,
            reasoning_effort=effort,
        )
    return captured


def test_minimax_payload_disables_thinking() -> None:
    body = _capture_minimax_payload("MiniMax-M3", thinking=False)
    assert body["thinking"] == {"type": "disabled"}
    assert body["reasoning_split"] is False
    assert "reasoning_effort" not in body


def test_minimax_payload_enables_thinking_with_split() -> None:
    body = _capture_minimax_payload("MiniMax-M3", thinking=True)
    assert body["thinking"] == {"type": "adaptive"}
    assert body["reasoning_split"] is True


def test_minimax_payload_forced_thinking_model_degrades() -> None:
    """M3.1-Flash-Preview 传 disabled 会 400 → 自动降级为 adaptive。"""
    body = _capture_minimax_payload("MiniMax-M3.1-Flash-Preview", thinking=False)
    assert body["thinking"] == {"type": "adaptive"}
    assert body["reasoning_split"] is True


def test_minimax_payload_reasoning_effort_top_level() -> None:
    body = _capture_minimax_payload("MiniMax-M3.1-Flash-Preview", thinking=True, effort="xhigh")
    assert body["reasoning_effort"] == "xhigh"
    # 非 M3.1-Flash 模型不传 reasoning_effort
    body2 = _capture_minimax_payload("MiniMax-M3", thinking=True, effort="xhigh")
    assert "reasoning_effort" not in body2


def test_minimax_payload_m2x_cannot_disable() -> None:
    body = _capture_minimax_payload("MiniMax-M2.7", thinking=False)
    assert "thinking" not in body


def test_minimax_payload_text01_no_thinking_fields() -> None:
    body = _capture_minimax_payload("MiniMax-Text-01", thinking=True, effort="high")
    assert "thinking" not in body
    assert "reasoning_split" not in body
    assert "reasoning_effort" not in body


def test_generate_retries_with_max_completion_tokens(monkeypatch) -> None:
    """A 400 asking for max_completion_tokens must be corrected and retried once."""
    calls = []

    class _Resp400:
        ok = False
        status_code = 400
        text = (
            '{"error":{"message":"Unsupported parameter: \'max_tokens\' is not '
            'supported with this model. Use \'max_completion_tokens\' instead.",'
            '"type":"invalid_request_error","param":"max_tokens",'
            '"code":"unsupported_parameter"}}'
        )

        def close(self):
            pass

    class _Resp200:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            }

    def _fake_post(url, headers, json, timeout, proxies):  # noqa: ANN001
        calls.append(dict(json))
        return _Resp400() if len(calls) == 1 else _Resp200()

    monkeypatch.setattr(llm_exec.requests, "post", _fake_post)

    exe = llm_exec.LLMAPIExecutor(
        baseURL="https://gw.example/v1", model="custom-reasoner"
    )
    content, _meta = exe.generate([{"role": "user", "content": "hi"}])

    assert content == "ok"
    assert len(calls) == 2
    assert "max_tokens" in calls[0] and "max_completion_tokens" not in calls[0]
    assert "max_completion_tokens" in calls[1] and "max_tokens" not in calls[1]


def test_generate_does_not_retry_auth_errors(monkeypatch) -> None:
    calls = []

    class _Resp401:
        ok = False
        status_code = 401
        text = '{"error":{"message":"invalid api key"}}'

        def close(self):
            pass

    def _fake_post(url, headers, json, timeout, proxies):  # noqa: ANN001
        calls.append(1)
        return _Resp401()

    monkeypatch.setattr(llm_exec.requests, "post", _fake_post)

    exe = llm_exec.LLMAPIExecutor(
        baseURL="https://gw.example/v1", model="custom-reasoner"
    )
    try:
        exe.generate([{"role": "user", "content": "hi"}])
    except llm_exec.LLMError:
        pass
    assert len(calls) == 1


def test_generate_drops_repetition_penalty_before_sending(monkeypatch) -> None:
    captured = {}

    class _Resp:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            }

    def _fake_post(url, headers, json, timeout, proxies):  # noqa: ANN001
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(llm_exec.requests, "post", _fake_post)
    monkeypatch.setenv(
        "SEED_LLM_EXTRA_BODY", '{"repetition_penalty": 1.1}'
    )

    exe = llm_exec.LLMAPIExecutor(
        baseURL="https://api.deepseek.com/v1", model="deepseek-4.1-flash"
    )
    content, _meta = exe.generate([{"role": "user", "content": "hi"}])

    assert content == "ok"
    assert "repetition_penalty" not in captured["json"]
