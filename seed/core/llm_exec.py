from __future__ import annotations

import os
from typing import Optional

from seed.core import env_access as _ea


class LLMAPIExecutor:
    """
    Executes LLM calls via OpenAI-compatible API.
    """
    
    def __init__(
        self,
        baseURL: str,
        model: str,
        maxOutputTokens: int = 8192,
        temperature: float = 0.7,
        topP: float = 0.95,
        topK: int = 40,
        api_key: Optional[str] = None,
        auth_scheme: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        """
        Initialize LLM API executor.
        
        Args:
            baseURL: API base URL (OpenAI-compatible, e.g., https://api.openai.com/v1)
            model: Model name to use
            maxOutputTokens: Maximum output tokens
            temperature: Temperature for sampling
            topP: Top-p sampling parameter
            topK: Top-k sampling parameter
        """
        self.baseURL = baseURL
        self.model = model
        self.temperature = temperature
        self.topP = topP
        self.topK = topK
        self.api_key = (
            api_key if api_key is not None else _ea.pick_nonempty(*_ea.LLM_API_KEY)
        )
        self.auth_scheme = (
            auth_scheme
            if auth_scheme is not None
            else (_ea.pick_nonempty(*_ea.LLM_AUTH_SCHEME) or "Bearer")
        )
        self.maxOutputTokens = int(
            _ea.pick_default(str(maxOutputTokens), *_ea.LLM_MAX_TOKENS)
        )
        from seed.core.model_providers import resolve_chat_protocol, apply_provider_chat_headers

        self.provider = (provider or "").strip()
        self.chat_protocol = resolve_chat_protocol(
            provider=self.provider, base_url=self.baseURL
        )
        self.headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            self.headers["Authorization"] = f"{self.auth_scheme} {self.api_key}"
        apply_provider_chat_headers(
            provider=self.provider,
            base_url=self.baseURL,
            headers=self.headers,
        )

    def _ensure_base_url(self) -> None:

        if not (self.baseURL or "").strip():
            raise LLMError(
                "未配置 LLM API 地址：请在 config/env 中设置 SEED_LLM_BASEURL（Code Agent 可在 config 中使用 CODEAGENT_LLM_BASEURL，由产品层 bridge 同步），"
                "或在 config/seed.models.json（旧文件名 codeagent.models.json）中至少保存一条含 Base URL 与模型的预设"
                "（未点「设为默认」时将自动使用列表中的第一条）。"
            )

    def _get_completion_url(self) -> str:
        """Get the completion endpoint URL"""
        if self.chat_protocol == "minimax_anthropic":
            base = (self.baseURL or "").rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            return f"{base}/v1/messages"
        return f"{self.baseURL}/chat/completions"
    



import copy
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests


logger = logging.getLogger(__name__)


def _requests_proxies_for(url: str):
    """Disable proxying for local LLM endpoints (Ollama / SGLang / etc.).

    A system/env proxy cannot reach loopback addresses, so requests to a local
    base URL must bypass it. Remote endpoints keep the default proxy behaviour.
    """
    try:
        from seed_model_providers import requests_proxies_for

        return requests_proxies_for(url)
    except Exception:
        return None


def generate(
    self,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    max_turns: int = 1,
    temperature: Optional[float] = None,
    temperature_reset: bool = False,
    max_tokens: Optional[int] = None,
    enable_thinking: Optional[bool] = None,
    reasoning_effort: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Generate response from LLM.
    
    Args:
        messages: List of messages in OpenAI format
        tools: Optional list of tool definitions
        max_turns: Number of completion turns
        temperature: Override temperature for this call
        temperature_reset: Whether to reset temperature after first turn
        max_tokens: If set, cap completion tokens for this call only
    
    Returns:
        Tuple of (content, metadata including tool_calls)
    """
    self._ensure_base_url()
    eff_max = self.maxOutputTokens if max_tokens is None else int(max_tokens)
    api_messages = _openai_chat_messages(
        messages, base_url=self.baseURL, chat_protocol=self.chat_protocol
    )
    params: Dict[str, Any] = {
        "model": self.model,
        "messages": copy.deepcopy(api_messages),
        "max_tokens": eff_max,
        "temperature": self.temperature if temperature is None else temperature,
        "top_p": self.topP,
    }
    if not _ea.any_nonempty(*_ea.LLM_NO_TOPK):
        params["top_k"] = self.topK

    if tools:
        params["tools"] = tools
        params["tool_choice"] = "auto"

    # --- Reasoning separation (provider-specific) -----
    extra_body: Dict[str, Any] = {}
    # Resolve enable_thinking from caller arg or env.
    if enable_thinking is None:
        env_val = _ea.pick_default("1", *_ea.LLM_ENABLE_THINKING)
        resolved_thinking = env_val.lower() not in ("0", "false", "no", "")
    else:
        resolved_thinking = bool(enable_thinking)

    from seed.core.model_providers import apply_chat_thinking_extra_body, apply_chat_stream_options

    apply_chat_thinking_extra_body(
        chat_protocol=self.chat_protocol,
        base_url=self.baseURL,
        params=params,
        extra_body=extra_body,
        resolved_thinking=resolved_thinking,
        reasoning_effort=reasoning_effort,
        model=self.model,
    )
    user_extra = _ea.pick_nonempty(*_ea.LLM_EXTRA_BODY)
    if user_extra:
        try:
            parsed = json.loads(user_extra)
            if isinstance(parsed, dict):
                # Deep-merge chat_template_kwargs so user can add/override keys.
                ctk = parsed.pop("chat_template_kwargs", None)
                extra_body.update(parsed)
                if isinstance(ctk, dict):
                    extra_body.setdefault("chat_template_kwargs", {}).update(ctk)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("SEED_LLM_EXTRA_BODY invalid JSON, ignored: %s", e)
    # Merge into top-level params (sglang accepts them at the request root).
    for k, v in extra_body.items():
        params.setdefault(k, v)

    max_body = max_llm_request_body_bytes(self.baseURL, chat_protocol=self.chat_protocol)
    if max_body > 0:
        maybe_shrink_llm_request_params(params, max_bytes=max_body, base_url=self.baseURL)

    # Clamp completion max_tokens using an estimated input budget. Prefer setting
    # SEED_LLM_CONTEXT_SIZE to the inference server's *effective* KV token pool
    # (e.g. SGLang ``max_total_num_tokens`` minus headroom), not always the model's
    # configured ``context_len`` when VRAM limits the cache.
    ctx = int(_ea.pick_default("262144", *_ea.LLM_CONTEXT_SIZE) or "262144")
    if ctx > 0:
        body = json.dumps(
            {"messages": params["messages"], "tools": tools or []},
            ensure_ascii=False,
        )
        div = int(_ea.pick_default("3", *_ea.LLM_INPUT_TOKEN_EST_DIVISOR))
        est_in = max(1, len(body.encode("utf-8")) // max(div, 1))
        margin = int(_ea.pick_default("8192", *_ea.LLM_CONTEXT_MARGIN))
        cap = ctx - est_in - margin
        req = int(params.get("max_tokens") or 0)
        if req > 0:
            allowance = max(1, cap) if cap > 0 else 1
            safe = max(1, min(req, allowance))
            if safe < req:
                logger.warning(
                    "Lowering max_tokens %s -> %s (est_input≈%s, ctx=%s, margin=%s)",
                    req,
                    safe,
                    est_in,
                    ctx,
                    margin,
                )
                params["max_tokens"] = safe

    # MiniMax-only precheck: ask the API for an exact input-token estimate
    # before paying for a request that may exceed the model's context window.
    if self.chat_protocol == "minimax":
        try:
            est = estimate_minimax_tokens(
                base_url=self.baseURL,
                api_key=self.api_key,
                auth_scheme=self.auth_scheme,
                model=self.model,
                messages=api_messages,
                tools=tools,
                reasoning_effort=reasoning_effort,
            )
            est_n = int(est.get("input_tokens") or 0)
            ctx_override = int(_ea.pick_default("0", *_ea.LLM_CONTEXT_SIZE) or "0") or None
            err = precheck_minimax_context(
                model=self.model,
                estimated_input_tokens=est_n,
                context_override=ctx_override,
            )
            if err:
                raise LLMError(err)
            if est_n >= MINIMAX_LONG_CONTEXT_THRESHOLD:
                logger.info(
                    "MiniMax '%s' long-context request: %d tokens (>= %d, "
                    "long-context pricing applies).",
                    self.model,
                    est_n,
                    MINIMAX_LONG_CONTEXT_THRESHOLD,
                )
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            # Precheck is best-effort; never block a real request on it.
            logger.debug("MiniMax input_tokens precheck skipped: %s", e)

    # Anthropic 主动缓存分支：把 OpenAI 风格 messages 转换为 Anthropic Messages
    # 协议 payload，自动给 system / tools / 最后一条历史消息打 cache_control。
    if self.chat_protocol == "minimax_anthropic":
        params = _to_minimax_anthropic_request(
            api_messages,
            model=self.model,
            max_tokens=int(params.get("max_tokens") or self.maxOutputTokens),
            tools=tools,
            enable_caching=True,
            extra_body=extra_body,
        )

    try:
        _url = self._get_completion_url()
        response = requests.post(
            _url,
            headers=self.headers,
            json=params,
            timeout=120,
            proxies=_requests_proxies_for(_url),
        )
        if not response.ok:
            snippet = (response.text or "")[:2000]
            raise LLMError(
                f"LLM HTTP {response.status_code} for {self._get_completion_url()}: {snippet}"
            )
        if self.chat_protocol == "minimax_anthropic":
            raw = response.json()
            data = _parse_minimax_anthropic_response(raw)
        else:
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"LLM returned no choices: {json.dumps(data)[:1200]}")

        msg = choices[0].get("message")
        if not isinstance(msg, dict):
            raise LLMError(f"LLM message missing or invalid: {choices[0]!r}")

        content = _msg_text_to_str(msg.get("content"))
        # sglang separate_reasoning populates `reasoning_content` (DeepSeek
        # API contract); some older/alt backends use `reasoning` or
        # `thinking`. Read all three and keep them OUT of `content`.
        reasoning_content = _msg_text_to_str(msg.get("reasoning_content") or "")
        reasoning_alt = _msg_text_to_str(msg.get("reasoning") or "")
        thinking = _msg_text_to_str(msg.get("thinking") or "")
        # MiniMax-M3 (with reasoning_split=True) returns reasoning in a
        # separate `reasoning_details` list. 旧版 MiniMax-M2.x / 未启用
        # reasoning_split 时，思考会内联在 `content` 中，被 <think>...</think>
        # 标签包裹；下面一并剥离。
        reasoning_details_text = _extract_reasoning_details_text(msg.get("reasoning_details"))
        stripped_content, inline_think_text = _strip_think_tags(content)
        if stripped_content != content:
            content = stripped_content
            if inline_think_text and not reasoning_details_text:
                reasoning_details_text = inline_think_text
        reasoning_parts = [
            s
            for s in (
                reasoning_content,
                reasoning_alt,
                thinking,
                reasoning_details_text,
            )
            if s.strip()
        ]
        reasoning = "\n\n".join(reasoning_parts)
        # IMPORTANT: do NOT fall back to copying `reasoning` into `content`.
        # That fallback historically caused raw CoT to leak into visible
        # chat output and pollute session history. If the server emits
        # reasoning-only (empty content), that's a server/model config
        # issue; we prefer an empty assistant turn + upstream rescue over
        # silent CoT leakage.
        reasoning_for_meta = reasoning
        if not content.strip() and reasoning.strip():
            # Keep content empty; surface the anomaly in logs so operators
            # notice missing `enable_thinking` / parser config.
            logger.warning(
                "LLM returned reasoning (%d chars) but empty content; "
                "check --reasoning-parser and chat_template enable_thinking.",
                len(reasoning),
            )

        tool_calls = _extract_tool_calls(msg)
        if tool_calls and not (content or "").strip():
            ph = assistant_toolcall_content_placeholder()
            if ph is not None:
                content = ph

        if temperature_reset:
            self.temperature = self.temperature

        # `reasoning_content` metadata: prefer API `reasoning_content`, else any
        # CoT-bearing field so persist/echo never drops DeepSeek's chain.
        reasoning_echo = (reasoning_content.strip() or reasoning.strip())
        raw_usage = data.get("usage", {})
        from seed.core.model_providers import normalize_chat_usage

        raw_usage = normalize_chat_usage(
            raw_usage if isinstance(raw_usage, dict) else {},
            chat_protocol=self.chat_protocol,
            provider=self.provider,
        )
        metadata = {
            "model": self.model,
            "usage": raw_usage,
            "tool_calls": tool_calls,
            "reasoning": reasoning_for_meta,
            "reasoning_content": reasoning_echo,
        }

        return content, metadata

    except LLMError:
        raise
    except requests.exceptions.RequestException as e:
        raise LLMError(f"Failed to call LLM API: {e}", original_error=e)
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Unexpected API response format: {e}", original_error=e)




import copy
import json
import logging
import os
from typing import Any, Dict, Generator, List, Optional

import requests


logger = logging.getLogger(__name__)


def generate_stream(
    self,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    enable_thinking: Optional[bool] = None,
    reasoning_effort: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Stream response from LLM, yielding SSE chunks as they arrive.

    Each yielded dict has ``type``:
      - ``"delta"``: ``{"type": "delta", "text": "..."}``
      - ``"reasoning_delta"``: ``{"type": "reasoning_delta", "text": "..."}``
      - ``"tool_calls"``: ``{"type": "tool_calls", "tool_calls": [...]}``
      - ``"done"``: ``{"type": "done", "content": "...", "metadata": {...}}``
    """
    self._ensure_base_url()
    eff_max = self.maxOutputTokens if max_tokens is None else int(max_tokens)
    api_messages = _openai_chat_messages(
        messages, base_url=self.baseURL, chat_protocol=self.chat_protocol
    )
    params: Dict[str, Any] = {
        "model": self.model,
        "messages": copy.deepcopy(api_messages),
        "max_tokens": eff_max,
        "temperature": self.temperature if temperature is None else temperature,
        "top_p": self.topP,
        "stream": True,
    }
    if not _ea.any_nonempty(*_ea.LLM_NO_TOPK):
        params["top_k"] = self.topK

    if tools:
        params["tools"] = tools
        params["tool_choice"] = "auto"

    # Reasoning / thinking params (same logic as generate())
    extra_body: Dict[str, Any] = {}
    if enable_thinking is None:
        env_val = _ea.pick_default("1", *_ea.LLM_ENABLE_THINKING)
        resolved_thinking = env_val.lower() not in ("0", "false", "no", "")
    else:
        resolved_thinking = bool(enable_thinking)

    from seed.core.model_providers import apply_chat_thinking_extra_body, apply_chat_stream_options

    apply_chat_thinking_extra_body(
        chat_protocol=self.chat_protocol,
        base_url=self.baseURL,
        params=params,
        extra_body=extra_body,
        resolved_thinking=resolved_thinking,
        reasoning_effort=reasoning_effort,
        model=self.model,
    )
    user_extra = _ea.pick_nonempty(*_ea.LLM_EXTRA_BODY)
    if user_extra:
        try:
            parsed = json.loads(user_extra)
            if isinstance(parsed, dict):
                ctk = parsed.pop("chat_template_kwargs", None)
                extra_body.update(parsed)
                if isinstance(ctk, dict):
                    extra_body.setdefault("chat_template_kwargs", {}).update(ctk)
        except (json.JSONDecodeError, TypeError):
            pass
    for k, v in extra_body.items():
        params.setdefault(k, v)

    apply_chat_stream_options(chat_protocol=self.chat_protocol, params=params)

    max_body = max_llm_request_body_bytes(self.baseURL, chat_protocol=self.chat_protocol)
    if max_body > 0:
        maybe_shrink_llm_request_params(params, max_bytes=max_body, base_url=self.baseURL)

    resp = None
    try:
        _url = self._get_completion_url()
        resp = requests.post(
            _url,
            headers=self.headers,
            json=params,
            stream=True,
            timeout=120,
            proxies=_requests_proxies_for(_url),
        )
        if not resp.ok:
            snippet = (resp.text or "")[:2000]
            raise LLMError(
                f"LLM HTTP {resp.status_code} for {self._get_completion_url()}: {snippet}"
            )

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls_accum: List[Dict[str, Any]] = []
        finish_reason: Optional[str] = None
        usage: Dict[str, Any] = {}

        # 强制 UTF-8 解码，兼容部分 SSE 服务端未返回 charset=utf-8 的情况
        resp.encoding = "utf-8"

        try:
            from seed.core.chat_events import is_chat_cancelled
        except Exception:
            is_chat_cancelled = lambda: False  # type: ignore[assignment, misc]

        for line in resp.iter_lines(decode_unicode=True):
            if is_chat_cancelled():
                break
            if not line or not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            if payload in ("[DONE]", ""):
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue

            # usage may appear in final chunk (sglang-style) or separate key — 
            # must extract BEFORE `if not choices: continue` so we don't lose it.
            raw_usage = chunk.get("usage")
            if raw_usage:
                usage = raw_usage

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            # finish_reason at the choice level
            fr = choices[0].get("finish_reason")
            if fr:
                finish_reason = fr

            # Content delta
            text = _msg_text_to_str(delta.get("content"))
            if text:
                content_parts.append(text)
                yield {"type": "delta", "text": text}

            # Reasoning content (sglang separate_reasoning / DeepSeek)
            rc = _msg_text_to_str(delta.get("reasoning_content"))
            if rc:
                reasoning_parts.append(rc)
                yield {"type": "reasoning_delta", "text": rc}

            # Tool calls (typically only in the final choice chunk)
            tc_raw = delta.get("tool_calls")
            if tc_raw:
                for i, tc in enumerate(tc_raw):
                    idx = tc.get("index", i)
                    while len(tool_calls_accum) <= idx:
                        tool_calls_accum.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    slot = tool_calls_accum[idx]
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    if tc.get("type"):
                        slot["type"] = tc["type"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

        full_content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)
        built_tool_calls = _extract_tool_calls({"tool_calls": tool_calls_accum}) if tool_calls_accum else []

        if built_tool_calls and not full_content.strip():
            ph = assistant_toolcall_content_placeholder()
            if ph is not None:
                full_content = ph

        reasoning_echo = reasoning.strip()
        stream_usage = usage
        from seed.core.model_providers import normalize_chat_usage

        stream_usage = normalize_chat_usage(
            stream_usage if isinstance(stream_usage, dict) else {},
            chat_protocol=self.chat_protocol,
            provider=self.provider,
        )
        metadata = {
            "model": self.model,
            "usage": stream_usage,
            "tool_calls": built_tool_calls,
            "reasoning": reasoning,
            "reasoning_content": reasoning_echo,
        }

        yield {
            "type": "done",
            "content": full_content,
            "reasoning": reasoning,
            "tool_calls": built_tool_calls,
            "metadata": metadata,
        }

    except LLMError:
        raise
    except requests.exceptions.RequestException as e:
        raise LLMError(f"Failed to call LLM API (stream): {e}", original_error=e)
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Unexpected API stream response: {e}", original_error=e)
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

def count_tokens(self, text: str) -> int:
    """
    Estimate token count (simple approximation).
    
    Args:
        text: Text to estimate tokens for
        
    Returns:
        Approximate token count
    """
    return len(text.encode('utf-8')) // 4


# Implemented as module-level functions (``self`` first arg) for historical file layout;
# expose them on the class so instances match ``llm.generate(...)`` / ``llm.generate_stream(...)``.
def ensure_llm_executor_methods() -> None:
    """Attach module-level implementations to :class:`LLMAPIExecutor` if missing."""
    if callable(getattr(LLMAPIExecutor, "generate_stream", None)):
        return
    LLMAPIExecutor.generate = generate  # type: ignore[assignment]
    LLMAPIExecutor.generate_stream = generate_stream  # type: ignore[assignment]
    LLMAPIExecutor.count_tokens = count_tokens  # type: ignore[assignment]


ensure_llm_executor_methods()


"""LLM API executor for CodeAgent - Connect to external LLM API"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

from seed.core import env_access as _ea

logger = logging.getLogger(__name__)


def _is_deepseek_url(base_url: Optional[str] = None) -> bool:
    """
    Legacy URL heuristic; prefer explicit ``provider`` / ``chat_protocol`` on executor.
    """
    from seed.core.model_providers import uses_deepseek_chat_protocol

    return uses_deepseek_chat_protocol(provider="", base_url=base_url or "")


def _openai_chat_messages(
    messages: List[Dict[str, Any]],
    *,
    base_url: Optional[str] = None,
    chat_protocol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Strip non-API keys (e.g. ``ts``) before POSTing to OpenAI-compatible endpoints.
    """
    def _should_send_reasoning_content() -> bool:
        """
        DeepSeek thinking-mode contract: assistant `reasoning_content` must be
        echoed on every subsequent request (often including non-tool replies),
        otherwise the API returns HTTP 400. Tool-call chains remain the strictest
        case; plain multi-turn chat in thinking mode also requires the field.

        Some strict OpenAI-compatible gateways reject unknown keys, so this is
        gated behind an env flag, with a safe auto-enable for DeepSeek's
        official endpoint.
        """
        from seed.core.model_providers import resolve_chat_protocol, should_send_reasoning_content

        proto = chat_protocol or resolve_chat_protocol(provider="", base_url=base_url or "")
        return should_send_reasoning_content(chat_protocol=proto, base_url=base_url or "")

    def _normalize_minimax_multimodal(content: Any) -> Any:
        """Normalize MiniMax Responses-style multimodal blocks → OpenAI style.

        MiniMax-M3 has two API surfaces:
          - ``/v1/responses``  uses ``input_text`` / ``input_image`` / ``input_video``
          - ``/v1/chat/completions`` uses ``text`` / ``image_url`` / ``video_url``

        When a caller mixes schemas (e.g. agent code wrote Responses-style blocks
        but the preset routes through chat/completions), we rewrite the
        ``type`` so the OpenAI-compatible endpoint accepts the payload.
        """
        if not isinstance(content, list):
            return content
        out_parts: List[Dict[str, Any]] = []
        changed = False
        for p in content:
            if not isinstance(p, dict):
                out_parts.append(p)
                continue
            ptype = p.get("type")
            if ptype == "input_text" and isinstance(p.get("text"), str):
                out_parts.append({"type": "text", "text": p["text"]})
                changed = True
            elif ptype == "output_text" and isinstance(p.get("text"), str):
                out_parts.append({"type": "text", "text": p["text"]})
                changed = True
            elif ptype == "input_image":
                src = p.get("image_url") or p.get("image")
                url = src.get("url") if isinstance(src, dict) else (src if isinstance(src, str) else None)
                detail = src.get("detail") if isinstance(src, dict) else None
                block: Dict[str, Any] = {"type": "image_url", "image_url": {"url": url or ""}}
                if detail:
                    block["image_url"]["detail"] = detail
                out_parts.append(block)
                changed = True
            elif ptype == "input_video":
                src = p.get("video_url") or p.get("video")
                url = src.get("url") if isinstance(src, dict) else (src if isinstance(src, str) else None)
                fps = src.get("fps") if isinstance(src, dict) else None
                vblock: Dict[str, Any] = {"type": "video_url", "video_url": {"url": url or ""}}
                if fps is not None:
                    vblock["video_url"]["fps"] = fps
                out_parts.append(vblock)
                changed = True
            else:
                out_parts.append(p)
        return out_parts if changed else content

    include_rc = _should_send_reasoning_content()
    from seed.core.model_providers import resolve_chat_protocol, uses_full_reasoning_content_echo

    _full_rc_echo = uses_full_reasoning_content_echo(
        chat_protocol=chat_protocol
        or resolve_chat_protocol(provider="", base_url=base_url or ""),
        base_url=base_url or "",
    )
    out: List[Dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "tool":
            row: Dict[str, Any] = {"role": "tool"}
            if "content" in m:
                row["content"] = m["content"]
            if m.get("tool_call_id") is not None:
                row["tool_call_id"] = m["tool_call_id"]
            if m.get("name"):
                row["name"] = m["name"]
            out.append(row)
            continue
        if role not in ("system", "user", "assistant"):
            continue
        row = {"role": role}
        if "content" in m:
            content = m["content"]
            if chat_protocol in ("minimax", "minimax_anthropic") or (
                not chat_protocol and base_url and "minimaxi.com" in base_url.lower()
            ):
                content = _normalize_minimax_multimodal(content)
            row["content"] = content
        if m.get("name"):
            row["name"] = m["name"]
        if m.get("tool_calls"):
            row["tool_calls"] = m["tool_calls"]
            if "content" not in row:
                row["content"] = None
        # DeepSeek: echo reasoning_content for every assistant turn when enabled
        # (not only tool chains); missing key → invalid_request_error.
        # Other backends: only send when we have tool_calls or a stored key, so
        # strict OpenAI-compatible proxies are not spammed with unknown fields.
        if include_rc and role == "assistant":
            if _full_rc_echo:
                rc = m.get("reasoning_content")
                row["reasoning_content"] = "" if rc is None else _msg_text_to_str(rc)
            elif m.get("tool_calls") or ("reasoning_content" in m):
                row["reasoning_content"] = (
                    m["reasoning_content"] if m.get("reasoning_content") is not None else ""
                )
        out.append(row)

    # After the last `user` in this request, if the tail contains any `tool`
    # message, DeepSeek requires every `assistant` in that tail to carry
    # `reasoning_content` (possibly ""). Fill missing keys so the request validates.
    if include_rc and out:
        last_user_i = -1
        for i, x in enumerate(out):
            if isinstance(x, dict) and x.get("role") == "user":
                last_user_i = i
        if last_user_i >= 0:
            tail = out[last_user_i + 1 :]
            if any(isinstance(x, dict) and x.get("role") == "tool" for x in tail):
                for row in tail:
                    if not isinstance(row, dict) or row.get("role") != "assistant":
                        continue
                    val = row.get("reasoning_content")
                    if val is None:
                        row["reasoning_content"] = ""
                    elif not isinstance(val, str):
                        row["reasoning_content"] = _msg_text_to_str(val)
    return out


def assistant_toolcall_content_placeholder() -> Optional[str]:
    """
    当模型在返回 ``tool_calls`` 时把 ``content`` 置空，部分 OpenAI 兼容栈或下游
    会把「空正文 + 工具」误判为异常并中断工具链。用非空占位符可稳定多轮。

    环境变量：
    - ``SEED_ASSISTANT_TOOLCALL_PLACEHOLDER_DISABLE=1``（或 ``CODEAGENT_*`` 别名）：不替换
    - ``SEED_ASSISTANT_TOOLCALL_PLACEHOLDER=...``：显式指定占位正文（可为空字符串）
    - 未设置 PLACEHOLDER 时：默认一个 ASCII 空格（对模型干扰最小）
    """
    dis = _ea.pick_default("", *_ea.ASSISTANT_TOOLCALL_PLACEHOLDER_DISABLE).lower()
    if dis in ("1", "true", "yes", "on"):
        return None
    for k in _ea.ASSISTANT_TOOLCALL_PLACEHOLDER:
        if k in os.environ:
            return os.environ[k]
    return " "


def _msg_text_to_str(raw: Any) -> str:
    """Normalize `message.content` (string, null, or multimodal list) to str."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: List[str] = []
        for item in raw:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif "text" in item:
                    parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(raw)


msg_text_to_str = _msg_text_to_str


# ---------------------------------------------------------------------------
# MiniMax-specific helpers: ``<think>`` tag stripping + reasoning_details
# ---------------------------------------------------------------------------

_THINK_TAG_RE = re.compile(r"<think>\s*(.*?)\s*</think>", re.IGNORECASE | re.DOTALL)


def _strip_think_tags(content: str) -> tuple[str, str]:
    """Strip ``<think>...</think>`` blocks from MiniMax-M2.x responses.

    Returns ``(cleaned_content, extracted_thinking)``.  The thinking text is
    concatenated from every ``<think>`` block (in order) so we can surface it
    alongside the visible reply instead of leaking raw CoT into chat history.
    Empty tuples are returned when the content has no think tag.
    """
    if not content or "<think>" not in content.lower():
        return content, ""
    parts: List[str] = []
    last_end = 0
    for m in _THINK_TAG_RE.finditer(content):
        parts.append(m.group(1))
        last_end = m.end()
    cleaned = _THINK_TAG_RE.sub("", content).strip()
    thinking = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return cleaned, thinking


def _extract_reasoning_details_text(raw: Any) -> str:
    """Flatten MiniMax-M3 ``reasoning_details`` list to a single text.

    Schema (when ``reasoning_split=True``):
        ``{"summary": [{"type": "summary_text", "text": "..."}]}``
    Older variants use ``{"reasoning_text": "..."}``.  Empty string on miss.
    """
    if raw is None:
        return ""
    pieces: List[str] = []
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        summary = raw.get("summary")
        if isinstance(summary, list):
            for s in summary:
                if isinstance(s, dict) and s.get("type") in ("summary_text", None):
                    t = s.get("text")
                    if isinstance(t, str) and t.strip():
                        pieces.append(t)
        rt = raw.get("reasoning_text")
        if isinstance(rt, str) and rt.strip():
            pieces.append(rt)
        # 兼容 list 元素直接传 dict 的形态：{"type": "summary_text", "text": "..."}
        if not pieces and raw.get("type") in ("summary_text", "reasoning_text", None) and isinstance(raw.get("text"), str):
            pieces.append(raw["text"])
        return "\n\n".join(pieces)
    if isinstance(raw, list):
        for item in raw:
            t = _extract_reasoning_details_text(item)
            if t:
                pieces.append(t)
        return "\n\n".join(pieces)
    return ""


def _to_minimax_anthropic_request(
    messages: List[Dict[str, Any]],
    *,
    model: str,
    max_tokens: int,
    tools: Optional[List[Dict[str, Any]]] = None,
    enable_caching: bool = True,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert OpenAI-style chat messages → Anthropic Messages API payload.

    Implements MiniMax Anthropic 主动缓存 by tagging the last block of
    ``system`` / ``tools`` / and the last historical message with
    ``cache_control: ephemeral`` (per Anthropic spec).  5 min TTL, auto-renewed
    on subsequent calls.
    """
    sys_blocks: List[Dict[str, Any]] = []
    api_messages: List[Dict[str, Any]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = m.get("content")
        if role == "system":
            text = content if isinstance(content, str) else _msg_text_to_str(content)
            sys_blocks.append({"type": "text", "text": str(text or "")})
            continue
        if role == "tool":
            api_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id") or "",
                            "content": content if isinstance(content, str) else _msg_text_to_str(content),
                        }
                    ],
                }
            )
            continue
        # user / assistant
        anth_role = "assistant" if role == "assistant" else "user"
        if isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            for p in content:
                if not isinstance(p, dict):
                    continue
                ptype = p.get("type")
                if ptype == "text" or isinstance(p.get("text"), str):
                    blocks.append({"type": "text", "text": str(p.get("text") or "")})
                elif ptype == "image_url":
                    blocks.append(_anthropic_image_block(p.get("image_url")))
                elif ptype == "video_url":
                    blocks.append(_anthropic_video_block(p.get("video_url")))
            api_messages.append({"role": anth_role, "content": blocks or [{"type": "text", "text": ""}]})
        else:
            text = content if isinstance(content, str) else _msg_text_to_str(content)
            api_messages.append({"role": anth_role, "content": str(text or "")})
        # Assistant tool_calls → tool_use blocks (extend the just-appended
        # assistant message; do NOT pop the previous one).
        if role == "assistant" and m.get("tool_calls") and api_messages:
            last = api_messages[-1]
            if last.get("role") == "assistant":
                if isinstance(last.get("content"), list):
                    content_blocks = list(last["content"])
                elif isinstance(last.get("content"), str) and last["content"]:
                    content_blocks = [{"type": "text", "text": last["content"]}]
                else:
                    content_blocks = []
                for tc in m.get("tool_calls") or []:
                    fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                    args = fn.get("arguments") or "{}"
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args)
                        except TypeError:
                            args = "{}"
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id") or f"toolu_{len(content_blocks)}",
                            "name": fn.get("name") or "",
                            "input": _safe_json_loads(args),
                        }
                    )
                last["content"] = content_blocks or [{"type": "text", "text": ""}]

    # Apply cache_control to the last blocks of system / tools / last message.
    if enable_caching and api_messages:
        # 1) system
        if sys_blocks:
            sys_blocks[-1]["cache_control"] = {"type": "ephemeral"}
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "messages": api_messages,
    }
    if sys_blocks:
        payload["system"] = sys_blocks
    elif enable_caching:
        # Even with no system prompt, send an empty one to anchor the cache.
        payload["system"] = [{"type": "text", "text": "", "cache_control": {"type": "ephemeral"}}]

    if tools:
        anth_tools: List[Dict[str, Any]] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            if not isinstance(fn, dict) or not fn.get("name"):
                continue
            anth_tools.append(
                {
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        if anth_tools:
            if enable_caching:
                anth_tools[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = anth_tools
    if enable_caching and api_messages:
        tail = api_messages[-1]
        if isinstance(tail.get("content"), list) and tail["content"]:
            tail["content"][-1].setdefault("cache_control", {"type": "ephemeral"})
        elif isinstance(tail.get("content"), str) and tail["content"]:
            # Anthropic requires a list of blocks; wrap the trailing string
            # and tag the new block for cache_control.
            tail["content"] = [
                {
                    "type": "text",
                    "text": tail["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]

    if extra_body:
        # Carry through thinking / reasoning_split etc.
        for k, v in extra_body.items():
            if k in ("reasoning_split",):
                continue
            payload.setdefault(k, v)
    return payload


def _safe_json_loads(s: str) -> Dict[str, Any]:
    try:
        loaded = json.loads(s)
    except (ValueError, TypeError):
        return {"_raw": s}
    return loaded if isinstance(loaded, dict) else {"_raw": loaded}


def _anthropic_image_block(image_url: Any) -> Dict[str, Any]:
    """Best-effort conversion: pass through URL or data URL."""
    if isinstance(image_url, str):
        return {"type": "image", "source": {"type": "url", "url": image_url}}
    if isinstance(image_url, dict):
        url = image_url.get("url") or ""
        if url.startswith("data:"):
            media_type, _, b64 = url.partition(";base64,")
            media_type = media_type[len("data:"):] if media_type.startswith("data:") else "image/jpeg"
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            }
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "text", "text": "[unsupported image input]"}


def _anthropic_video_block(video_url: Any) -> Dict[str, Any]:
    """MiniMax accepts video_url on /anthropic/v1/messages (MP4/AVI/MOV/MKV)."""
    if isinstance(video_url, str):
        return {"type": "video", "source": {"type": "url", "url": video_url}}
    if isinstance(video_url, dict):
        return {"type": "video", "source": {"type": "url", "url": video_url.get("url") or ""}}
    return {"type": "text", "text": "[unsupported video input]"}


def _parse_minimax_anthropic_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Anthropic Messages response → OpenAI-style chat completion shape
    so the rest of the LLM stack (tool_calls / reasoning extraction) keeps working.
    """
    if not isinstance(data, dict):
        raise LLMError("MiniMax Anthropic response invalid: not a dict")
    content = data.get("content") or []
    text_parts: List[str] = []
    thinking_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(str(block.get("text") or ""))
        elif btype == "thinking":
            thinking_parts.append(str(block.get("thinking") or ""))
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or f"toolu_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
    stop_reason = data.get("stop_reason") or "end_turn"
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": stop_reason,
                "message": {
                    "role": "assistant",
                    "content": "".join(text_parts),
                    "tool_calls": tool_calls or None,
                    "reasoning_content": "\n\n".join(thinking_parts) if thinking_parts else "",
                },
            }
        ],
        "usage": data.get("usage") or {},
        "model": data.get("model") or "",
    }


def normalize_minimax_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible alias — prefer ``normalize_chat_usage``."""
    from seed.core.model_providers import normalize_chat_usage

    return normalize_chat_usage(usage, chat_protocol="minimax", provider="minimax")


MINIMAX_CONTEXT_WINDOWS: Dict[str, int] = {
    "MiniMax-M3": 1_000_000,
    "MiniMax-M2.7": 204_800,
    "MiniMax-M2.7-highspeed": 204_800,
    "MiniMax-M2.5": 204_800,
    "MiniMax-M2.5-highspeed": 204_800,
    "MiniMax-M2.1": 204_800,
    "MiniMax-M2.1-highspeed": 204_800,
    "MiniMax-M2": 204_800,
    "MiniMax-M2-Her": 64_000,
    "MiniMax-Text-01": 4_000_000,
    "MiniMax-VL-01": 4_000_000,
}
# 512K is the threshold where MiniMax-M3 starts charging the long-context
# price.  See https://platform.minimaxi.com/docs/api-reference/text-prompt-caching
MINIMAX_LONG_CONTEXT_THRESHOLD = 512_000


def _minimax_input_tokens_url(base_url: str) -> str:
    """Resolve MiniMax token-estimation endpoint from preset base_url."""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/v1/responses/input_tokens"):
        return base
    if base.endswith("/v1/responses"):
        return f"{base}/input_tokens"
    if base.endswith("/v1"):
        return f"{base}/responses/input_tokens"
    return f"{base}/v1/responses/input_tokens"


def _to_minimax_responses_input(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Convert OpenAI-style chat messages to MiniMax Responses API ``input`` items.

    Only the fields the input-tokens endpoint needs are populated; the endpoint
    does not actually invoke the model, so we keep the payload small.
    """
    out: List[Dict[str, Any]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        content = m.get("content")
        if isinstance(content, list):
            parts: List[Dict[str, Any]] = []
            for p in content:
                if isinstance(p, dict):
                    if p.get("type") == "text":
                        parts.append({"type": "input_text", "text": str(p.get("text") or "")})
                    elif p.get("type") == "image_url":
                        parts.append({"type": "input_image", "image_url": p.get("image_url") or ""})
                    elif p.get("type") == "video_url":
                        parts.append({"type": "input_video", "video_url": p.get("video_url") or ""})
                    elif isinstance(p.get("text"), str):
                        parts.append({"type": "input_text", "text": str(p.get("text") or "")})
                elif isinstance(p, str):
                    parts.append({"type": "input_text", "text": p})
            out.append({"type": "message", "role": role, "content": parts})
        else:
            out.append({"type": "message", "role": role, "content": str(content or "")})
    out_tools: List[Dict[str, Any]] = []
    if tools:
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            if not isinstance(fn, dict) or not fn.get("name"):
                continue
            out_tools.append(
                {
                    "type": "function",
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
    return out, out_tools


def estimate_minimax_tokens(
    *,
    base_url: str,
    api_key: str,
    auth_scheme: str = "Bearer",
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    reasoning_effort: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Call ``POST /v1/responses/input_tokens`` to estimate prompt size.

    Returns ``{"input_tokens": int, "raw": dict}``.  Raises ``LLMError`` on
    transport failure or invalid response.
    """
    if not api_key:
        raise LLMError("MiniMax input_tokens estimate requires an API key")
    input_items, out_tools = _to_minimax_responses_input(messages, tools)
    payload: Dict[str, Any] = {"model": model, "input": input_items}
    if out_tools:
        payload["tools"] = out_tools
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    url = _minimax_input_tokens_url(base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"{auth_scheme} {api_key}",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise LLMError(f"MiniMax input_tokens request failed: {e}", original_error=e)
    if resp.status_code >= 400:
        try:
            body = resp.json()
            detail = body.get("base_resp", {}).get("status_msg") if isinstance(body, dict) else None
        except Exception:
            detail = None
        raise LLMError(
            f"MiniMax input_tokens failed ({resp.status_code}): {detail or resp.text[:500]}"
        )
    try:
        body = resp.json()
    except ValueError as e:
        raise LLMError(f"MiniMax input_tokens returned invalid JSON: {e}", original_error=e)
    if not isinstance(body, dict):
        raise LLMError("MiniMax input_tokens returned unexpected body")
    n = body.get("input_tokens")
    if not isinstance(n, (int, float)):
        raise LLMError(f"MiniMax input_tokens missing input_tokens: {body!r}")
    return {"input_tokens": int(n), "raw": body}


def precheck_minimax_context(
    *,
    model: str,
    estimated_input_tokens: int,
    context_override: Optional[int] = None,
) -> Optional[str]:
    """Return an error message if ``estimated_input_tokens`` exceeds the
    model's context window; ``None`` when within budget.

    ``context_override`` lets callers inject a custom budget (e.g. via
    ``SEED_LLM_CONTEXT_SIZE``); otherwise the catalog mapping is used.
    """
    if context_override is not None and context_override > 0:
        window = int(context_override)
    else:
        window = MINIMAX_CONTEXT_WINDOWS.get(str(model) or "", 0)
    if window <= 0:
        return None
    if estimated_input_tokens > window:
        return (
            f"Estimated input tokens ({estimated_input_tokens}) exceed model "
            f"'{model}' context window ({window}). Shorten the conversation "
            "or switch to a larger-context model."
        )
    return None


def _extract_tool_calls(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse tool_calls; tolerate missing ids or oddly-shaped function payloads.

    **Argument validation**: ``function.arguments`` must be a non-empty string of
    valid JSON.  This is the exact contract the LLM API (OpenAI / DeepSeek /
    MiniMax / Minimax / SGLang ...) validates on the *next* turn when it sees the
    historical ``tool_calls`` echoed back.  If a streaming response was truncated
    (network blip, token limit, mid-arg server drop) the accumulated
    ``function.arguments`` can be ``""`` or partial JSON.  Persisting that into
    message history poisons the conversation: the next LLM call returns HTTP 400
    ``invalid function arguments json string`` and the chat dies.

    We drop such malformed tool_calls here so the loop can keep going (e.g. the
    model just emits plain text on the next turn) instead of carrying a time bomb
    forward.
    """
    raw = msg.get("tool_calls")
    if not raw:
        return []
    out: List[Dict[str, Any]] = []
    for i, tc in enumerate(raw):
        if not isinstance(tc, dict):
            continue
        tid = tc.get("id") or f"call_{i}"
        fn = tc.get("function")
        if isinstance(fn, str):
            try:
                fn = json.loads(fn)
            except (json.JSONDecodeError, TypeError):
                fn = {"name": "", "arguments": "{}"}
        if not isinstance(fn, dict):
            fn = {"name": "", "arguments": "{}"}
        # --- Validate arguments is non-empty valid JSON ---
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            if not raw_args.strip():
                logger.warning(
                    "Dropping tool_call id=%r name=%r: arguments is empty "
                    "(likely streaming truncation).",
                    tid, fn.get("name") or "",
                )
                continue
            try:
                json.loads(raw_args)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(
                    "Dropping tool_call id=%r name=%r: arguments is not valid JSON (%s). "
                    "Preview: %s",
                    tid, fn.get("name") or "", e, raw_args[:200],
                )
                continue
        else:
            # Provider gave us a dict / other non-string; normalize to a
            # canonical JSON string.  json.dumps("{}") is still valid.
            try:
                raw_args = json.dumps(raw_args if raw_args is not None else {}, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                logger.warning(
                    "Dropping tool_call id=%r name=%r: arguments is not JSON-serializable (%s).",
                    tid, fn.get("name") or "", e,
                )
                continue
        out.append(
            {
                "id": tid,
                "type": tc.get("type") or "function",
                "function": {
                    "name": fn.get("name") or "",
                    "arguments": raw_args,
                },
            }
        )
    return out


class LLMError(Exception):
    """Exception raised for LLM API errors"""
    
    def __init__(self, message: str, original_error: Optional[Exception] = None):
        self.message = message
        self.original_error = original_error
        super().__init__(message)





import os
from typing import Optional, Tuple


_default_executor: Optional[LLMAPIExecutor] = None
_executor_env_key: Optional[Tuple[str, str, str, str, str, str]] = None


def get_llm_executor(
    baseURL: Optional[str] = None,
    model: Optional[str] = None,
    *,
    api_key: Optional[str] = None,
    auth_scheme: Optional[str] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
) -> LLMAPIExecutor:
    """
    Get or create default LLM executor.

    Args:
        baseURL: Override baseURL from environment (``None`` = read env)
        model: Override model name from environment
        api_key: Override API key from environment (non-empty only)
        auth_scheme: Override auth scheme from environment (non-empty only)
        max_tokens: Override ``SEED_LLM_MAX_TOKENS`` when set

    Returns:
        LLMAPIExecutor instance
    """
    global _default_executor, _executor_env_key

    resolved_url = (
        (baseURL if baseURL is not None else _ea.pick_nonempty(*_ea.LLM_BASEURL))
        .strip()
        .rstrip("/")
    )
    if model is not None and str(model).strip():
        resolved_model = str(model).strip()
    else:
        resolved_model = _ea.pick_default(
            "Qwen/Qwen3.5-35B-A3B-GPTQ-Int4", *_ea.LLM_MODEL
        )

    max_tok = (
        str(int(max_tokens))
        if max_tokens is not None
        else _ea.pick_default("8192", *_ea.LLM_MAX_TOKENS)
    )

    resolved_api = _ea.pick_nonempty(*_ea.LLM_API_KEY)
    if api_key is not None and api_key.strip():
        resolved_api = api_key.strip()

    resolved_scheme = (_ea.pick_nonempty(*_ea.LLM_AUTH_SCHEME) or "Bearer")
    if auth_scheme is not None and auth_scheme.strip():
        resolved_scheme = auth_scheme.strip()

    resolved_provider = (provider or "").strip()
    key = (
        resolved_url,
        resolved_model,
        max_tok,
        resolved_api,
        resolved_scheme,
        resolved_provider,
    )

    if _default_executor is None or _executor_env_key != key:
        _default_executor = LLMAPIExecutor(
            baseURL=resolved_url,
            model=resolved_model,
            api_key=resolved_api,
            auth_scheme=resolved_scheme,
            maxOutputTokens=int(max_tok),
            provider=resolved_provider or None,
        )
        _executor_env_key = key

    return _default_executor


def reset_llm_executor() -> None:
    """Reset the default executor"""
    global _default_executor, _executor_env_key
    _default_executor = None
    _executor_env_key = None


"""Shrink chat-completions JSON before HTTP POST to avoid gateway 413 (body too large)."""


import json
import logging
import os
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


def request_json_size(params: Dict[str, Any]) -> int:
    return len(json.dumps(params, ensure_ascii=False).encode("utf-8"))


def max_llm_request_body_bytes(
    base_url: Optional[str],
    *,
    chat_protocol: Optional[str] = None,
) -> int:
    """0 = disabled. Official DeepSeek API often sits behind a ~1MiB reverse-proxy limit."""
    from seed.core.model_providers import default_max_request_body_bytes, resolve_chat_protocol

    raw = _ea.pick_nonempty(*_ea.LLM_MAX_REQUEST_BODY_BYTES)
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("Invalid SEED_LLM_MAX_REQUEST_BODY_BYTES=%r", raw)
    proto = chat_protocol or resolve_chat_protocol(provider="", base_url=base_url or "")
    return default_max_request_body_bytes(proto, base_url or "")


def maybe_shrink_llm_request_params(
    params: Dict[str, Any],
    *,
    max_bytes: int,
    base_url: Optional[str],
) -> None:
    """Mutates ``params`` (especially ``messages``) in place."""
    if max_bytes <= 0:
        return
    try:
        before = request_json_size(params)
    except (TypeError, ValueError):
        return
    if before <= max_bytes:
        return

    msgs = params.get("messages")
    if not isinstance(msgs, list):
        return

    last_ai = -1
    for i in range(len(msgs) - 1, -1, -1):
        if isinstance(msgs[i], dict) and msgs[i].get("role") == "assistant":
            last_ai = i
            break

    base_cap = int(_ea.pick_default("48000", *_ea.TOOL_OUTPUT_MAX_CHARS))
    base_cap = max(500, min(base_cap, 200_000))
    tool_caps: List[int] = []
    x = base_cap
    while x >= 500:
        tool_caps.append(x)
        x //= 2
    if not tool_caps or tool_caps[-1] != 500:
        tool_caps.append(500)

    def sz() -> int:
        return request_json_size(params)

    def trunc_tools(cap: int) -> None:
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "tool":
                content = m.get("content")
                if isinstance(content, str) and len(content) > cap:
                    drop = len(content) - cap
                    m["content"] = (
                        content[:cap]
                        + f"\n...[truncated {drop} chars for HTTP request body limit]"
                    )

    def trunc_rc(cap: int) -> None:
        from seed.core.model_providers import resolve_chat_protocol

        proto = resolve_chat_protocol(provider="", base_url=base_url or "")
        if proto != "deepseek":
            return
        for i, m in enumerate(msgs):
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            if i == last_ai:
                continue
            rc = m.get("reasoning_content")
            if not isinstance(rc, str) or not rc:
                continue
            if cap <= 0:
                m["reasoning_content"] = ""
            elif len(rc) > cap:
                drop = len(rc) - cap
                m["reasoning_content"] = (
                    rc[:cap] + f"\n...[truncated reasoning {drop} chars for HTTP body limit]"
                )

    def trunc_asst_content(cap: int) -> None:
        for i, m in enumerate(msgs):
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            if i == last_ai:
                continue
            ctn = m.get("content")
            if isinstance(ctn, str) and len(ctn) > cap:
                drop = len(ctn) - cap
                m["content"] = ctn[:cap] + f"\n...[truncated {drop} chars for HTTP body limit]"

    def trunc_system(cap: int) -> None:
        if not msgs or not isinstance(msgs[0], dict) or msgs[0].get("role") != "system":
            return
        ctn = msgs[0].get("content")
        if isinstance(ctn, str) and len(ctn) > cap:
            drop = len(ctn) - cap
            msgs[0]["content"] = ctn[:cap] + f"\n...[truncated system prompt {drop} chars]"

    for cap in tool_caps:
        trunc_tools(cap)
        if sz() <= max_bytes:
            break
    else:
        for cap in (16000, 8000, 4000, 2000, 800, 0):
            trunc_rc(cap)
            if sz() <= max_bytes:
                break
        else:
            for cap in (50000, 20000, 8000, 2000):
                trunc_asst_content(cap)
                if sz() <= max_bytes:
                    break
            else:
                for cap in (40000, 20000, 10000, 5000):
                    trunc_system(cap)
                    if sz() <= max_bytes:
                        break

    after = sz()
    if after <= max_bytes:
        logger.warning(
            "Shrunk LLM JSON request body %s -> %s bytes (limit %s) to reduce HTTP 413 risk",
            before,
            after,
            max_bytes,
        )
    else:
        logger.warning(
            "LLM JSON request body still ~%s bytes (limit %s). "
            "Enable SEED_CONTEXT_COMPACT=1, lower SEED_CHAT_USER_ROUNDS / "
            "SEED_TOOL_OUTPUT_MAX_CHARS, or set SEED_LLM_MAX_REQUEST_BODY_BYTES=0 "
            "only if your gateway allows larger uploads.",
            after,
            max_bytes,
        )
