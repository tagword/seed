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
