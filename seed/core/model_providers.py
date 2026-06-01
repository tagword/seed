"""
Model provider catalog and protocol resolution.

Presets may set ``provider`` explicitly; when omitted, provider is inferred from
``base_url`` (legacy behavior, e.g. DeepSeek host → deepseek chat protocol).

Chat protocols:
  - ``deepseek``: native thinking + reasoning_content echo
  - ``sglang``: separate_reasoning + chat_template_kwargs
  - ``minimax``: reasoning_details + thinking tag stripping + reasoning_split opt-in
  - ``openai``: minimal OpenAI-compatible payload

Image protocols:
  - ``openai_images``: POST {base}/images/generations (OpenAI DALL·E 等)
  - ``volcengine_images``: POST {base}/images/generations (火山方舟 Seedream，参数语义不同)
  - ``minimax_image``: POST {host}/v1/image_generation (MiniMax 开放平台)
  - ``minimax_music``: POST {host}/v1/music_generation (MiniMax 音乐生成)
  - ``minimax_video``: POST {host}/v1/video_generation (MiniMax Hailuo 视频生成)
  - ``agnes_video``: POST {base}/videos + poll GET {base}/videos/{task_id} (Agnes 视频生成)
  - ``none``: image_generate not supported for this provider unless preset uses a proxy

Speech (T2A) — MiniMax only today:
  - ``audio`` use_type: STT / transcription (``supports_audio``, e.g. whisper-1)
  - ``speech`` use_type: TTS / bubble playback (``supports_speech``, e.g. speech-2.8-turbo)
  - ``music`` use_type: song generation (``supports_music``, e.g. music-2.6)
  - ``video_gen`` use_type: AI video generation (``supports_video_gen``, e.g. hailuo-2.3 / agnes-video-v2.0)
  - MiniMax chat preset api_key is shared for chat, image, speech, music, and video endpoints
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Catalog (defaults for Web UI + protocol dispatch)
# ---------------------------------------------------------------------------

USE_TYPE_LABELS: Dict[str, str] = {
    "chat": "对话",
    "image": "生图",
    "vision": "识图",
    "audio": "音频",
    "speech": "朗读",
    "music": "音乐",
    "video_gen": "视频生成",
}

# 各服务商固定模型列表（Web 下拉选择；保存时自动写入 base_url / supports_*）
PROVIDER_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "description": "官方 V4 API；思考模式 extra_body.thinking + reasoning_effort（见思考模式文档）",
        "default_base_url": "https://api.deepseek.com/v1",
        "default_chat_model": "deepseek-v4-flash",
        "default_image_model": "",
        "chat_protocol": "deepseek",
        "image_protocol": "none",
        "capabilities": ["chat"],
        "requires_api_key": True,
        "models": {
            "chat": [
                {
                    "id": "deepseek-v4-flash",
                    "label": "deepseek-v4-flash",
                },
                {
                    "id": "deepseek-v4-pro",
                    "label": "deepseek-v4-pro",
                },
            ],
        },
    },
    {
        "id": "volcengine",
        "label": "火山方舟 (Seedream)",
        "description": "豆包 / 方舟；生图 model 填模型列表中的 Model ID（见文档 1330310）",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_chat_model": "doubao-seedream-5-0-lite-260128",
        "default_image_model": "doubao-seedream-5-0-lite-260128",
        "chat_protocol": "openai",
        "image_protocol": "volcengine_images",
        "capabilities": ["chat", "image"],
        "requires_api_key": True,
        "models": {
            "chat": [
                {"id": "MiniMax-M2.7", "label": "MiniMax-M2.7"},
                {"id": "MiniMax-M2.7-highspeed", "label": "MiniMax-M2.7 高速"},
                {"id": "MiniMax-M2.5", "label": "MiniMax-M2.5"},
            ],
            "image": [
                {"id": "doubao-seedream-5-0-lite-260128", "label": "Seedream 5.0 lite"},
                {"id": "doubao-seedream-5-0-260128", "label": "Seedream 5.0"},
                {"id": "doubao-seedream-4-5-251128", "label": "Seedream 4.5"},
                {"id": "doubao-seedream-4-0-250828", "label": "Seedream 4.0"},
            ],
        },
    },
    {
        "id": "minimax",
        "label": "MiniMax",
        "description": "MiniMax 开放平台；聊天、生图、朗读（T2A）、音乐、视频生成共用 API Key",
        "default_base_url": "https://api.minimaxi.com/v1",
        "default_chat_model": "MiniMax-M2.7",
        "default_image_model": "image-01",
        "default_speech_model": "speech-2.8-turbo",
        "default_music_model": "music-2.6",
        "default_video_gen_model": "hailuo-2.3",
        "chat_protocol": "minimax",
        "image_protocol": "minimax_image",
        "music_protocol": "minimax_music",
        "video_protocol": "minimax_video",
        "capabilities": ["chat", "image", "speech", "music", "video_gen"],
        "requires_api_key": True,
        "models": {
            "chat": [
                {"id": "MiniMax-M3", "label": "MiniMax-M3（最新旗舰，1M tokens）"},
                {"id": "MiniMax-M2.7", "label": "MiniMax-M2.7"},
                {"id": "MiniMax-M2.7-highspeed", "label": "MiniMax-M2.7 高速"},
                {"id": "MiniMax-M2.5", "label": "MiniMax-M2.5"},
                {"id": "MiniMax-M2.5-highspeed", "label": "MiniMax-M2.5 高速"},
                {"id": "MiniMax-M2.1", "label": "MiniMax-M2.1"},
                {"id": "MiniMax-M2.1-highspeed", "label": "MiniMax-M2.1 高速"},
                {"id": "MiniMax-M2", "label": "MiniMax-M2（开源）"},
                {"id": "MiniMax-M2-Her", "label": "MiniMax-M2-Her（角色扮演）"},
                {"id": "MiniMax-Text-01", "label": "MiniMax-Text-01（4M tokens，超长上下文）"},
                {"id": "MiniMax-VL-01", "label": "MiniMax-VL-01（视觉多模态，4M tokens）"},
            ],
            "image": [
                {"id": "image-01", "label": "image-01"},
                {"id": "image-01-live", "label": "image-01-live"},
            ],
            "speech": [
                {"id": "speech-2.8-turbo", "label": "2.8 Turbo（快）"},
                {"id": "speech-2.8-hd", "label": "2.8 HD（高质量）"},
                {"id": "speech-2.6-turbo", "label": "2.6 Turbo"},
                {"id": "speech-2.6-hd", "label": "2.6 HD"},
                {"id": "speech-2.5-turbo", "label": "2.5 Turbo"},
                {"id": "speech-2.5-hd", "label": "2.5 HD"},
                {"id": "speech-02-turbo", "label": "02 Turbo"},
                {"id": "speech-02-hd", "label": "02 HD"},
                {"id": "speech-01-hd", "label": "01 HD（早期）"},
            ],
            "music": [
                {"id": "music-2.6", "label": "Music 2.6（推荐）"},
                {"id": "music-2.6-free", "label": "Music 2.6 Free"},
                {"id": "music-cover", "label": "Music Cover（翻唱）"},
                {"id": "music-cover-free", "label": "Music Cover Free"},
            ],
            "video_gen": [
                {"id": "hailuo-2.3", "label": "Hailuo 2.3（最新）"},
                {"id": "hailuo-2.3-fast", "label": "Hailuo 2.3 Fast"},
                {"id": "hailuo-02", "label": "Hailuo 02"},
            ],
        },
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "description": "官方 OpenAI API",
        "default_base_url": "https://api.openai.com/v1",
        "default_chat_model": "gpt-4o",
        "default_image_model": "dall-e-3",
        "chat_protocol": "openai",
        "image_protocol": "openai_images",
        "capabilities": ["chat", "vision", "image"],
        "requires_api_key": True,
        "models": {
            "chat": [
                {"id": "gpt-4o", "label": "GPT-4o"},
                {"id": "gpt-4o-mini", "label": "GPT-4o mini"},
            ],
            "image": [
                {"id": "dall-e-3", "label": "DALL·E 3"},
                {"id": "dall-e-2", "label": "DALL·E 2"},
            ],
            "vision": [
                {"id": "gpt-4o", "label": "GPT-4o（识图）"},
            ],
        },
    },
    {
        "id": "agnes",
        "label": "Agnes AI",
        "description": "Agnes AI Gateway；异步视频生成 agnes-video-v2.0",
        "default_base_url": "https://apihub.agnes-ai.com/v1",
        "default_chat_model": "",
        "default_video_gen_model": "agnes-video-v2.0",
        "chat_protocol": "openai",
        "image_protocol": "none",
        "video_protocol": "agnes_video",
        "capabilities": ["video_gen"],
        "requires_api_key": True,
        "models": {
            "video_gen": [
                {"id": "agnes-video-v2.0", "label": "Agnes Video V2.0"},
            ],
        },
    },
    {
        "id": "openai_compatible",
        "label": "OpenAI 兼容",
        "description": "通用网关 / 自建 OpenAI 格式服务",
        "default_base_url": "http://127.0.0.1:8000/v1",
        "default_chat_model": "",
        "default_image_model": "",
        "chat_protocol": "openai",
        "image_protocol": "openai_images",
        "capabilities": ["chat", "vision", "image"],
        "requires_api_key": False,
        "models": {},
    },
    {
        "id": "sglang",
        "label": "SGLang / 本地推理",
        "description": "SGLang OpenAI 兼容；separate_reasoning + enable_thinking",
        "default_base_url": "http://127.0.0.1:30000/v1",
        "default_chat_model": "",
        "default_image_model": "",
        "chat_protocol": "sglang",
        "image_protocol": "openai_images",
        "capabilities": ["chat", "vision"],
        "requires_api_key": False,
        "models": {},
    },
    {
        "id": "azure_openai",
        "label": "Azure OpenAI",
        "description": "Azure 部署端点（需在 Base URL 填写完整 deployment URL）",
        "default_base_url": "",
        "default_chat_model": "",
        "default_image_model": "dall-e-3",
        "chat_protocol": "openai",
        "image_protocol": "openai_images",
        "capabilities": ["chat", "vision", "image"],
        "requires_api_key": True,
        "models": {
            "chat": [{"id": "gpt-4o", "label": "部署模型名（与 Azure 一致）"}],
            "image": [{"id": "dall-e-3", "label": "DALL·E 部署名"}],
        },
    },
    {
        "id": "ollama",
        "label": "Ollama",
        "description": "本地 Ollama OpenAI 兼容层",
        "default_base_url": "http://127.0.0.1:11434/v1",
        "default_chat_model": "",
        "default_image_model": "",
        "chat_protocol": "openai",
        "image_protocol": "none",
        "capabilities": ["chat", "vision"],
        "requires_api_key": False,
        "models": {},
    },
    {
        "id": "custom",
        "label": "自定义",
        "description": "手动填写 Base URL / 模型；协议按 URL 推断",
        "default_base_url": "",
        "default_chat_model": "",
        "default_image_model": "",
        "chat_protocol": "openai",
        "image_protocol": "openai_images",
        "capabilities": ["chat"],
        "requires_api_key": False,
        "models": {},
    },
]

_CATALOG_BY_ID: Dict[str, Dict[str, Any]] = {
    str(p["id"]): p for p in PROVIDER_CATALOG if p.get("id")
}


def list_provider_catalog() -> List[Dict[str, Any]]:
    """Public catalog for Web UI (no secrets)."""
    out: List[Dict[str, Any]] = []
    for p in PROVIDER_CATALOG:
        out.append(
            {
                "id": p["id"],
                "label": p.get("label") or p["id"],
                "description": p.get("description") or "",
                "default_base_url": p.get("default_base_url") or "",
                "default_chat_model": p.get("default_chat_model") or "",
                "default_image_model": p.get("default_image_model") or "",
                "default_speech_model": p.get("default_speech_model") or "",
                "default_music_model": p.get("default_music_model") or "",
                "default_video_gen_model": p.get("default_video_gen_model") or "",
                "chat_protocol": p.get("chat_protocol") or "openai",
                "image_protocol": p.get("image_protocol") or "none",
                "music_protocol": p.get("music_protocol") or "none",
                "video_protocol": p.get("video_protocol") or "none",
                "capabilities": list(p.get("capabilities") or []),
                "requires_api_key": bool(p.get("requires_api_key", True)),
                "models": dict(p.get("models") or {}),
                "use_type_labels": USE_TYPE_LABELS,
            }
        )
    return out


def provider_requires_api_key(provider_id: str) -> bool:
    return bool(get_provider_spec(provider_id).get("requires_api_key", True))


def list_models_for_provider(provider_id: str, use_type: str) -> List[Dict[str, str]]:
    spec = get_provider_spec(provider_id)
    bucket = spec.get("models") if isinstance(spec.get("models"), dict) else {}
    raw = bucket.get(use_type) if isinstance(bucket, dict) else None
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for m in raw:
        if isinstance(m, dict) and m.get("id"):
            out.append({"id": str(m["id"]), "label": str(m.get("label") or m["id"])})
    return out


def infer_preset_use_type(preset: Dict[str, Any]) -> str:
    ut = str(preset.get("use_type") or "").strip().lower()
    if ut in USE_TYPE_LABELS:
        return ut
    if preset.get("supports_image_gen") is True:
        return "image"
    if preset.get("supports_audio") is True:
        return "audio"
    if preset.get("supports_speech") is True:
        return "speech"
    if preset.get("supports_music") is True:
        return "music"
    if preset.get("supports_video_gen") is True:
        return "video_gen"
    if preset.get("supports_vision") is True:
        return "vision"
    return "chat"


def infer_use_type_for_provider_model(provider_id: str, model_id: str) -> Optional[str]:
    """Catalog bucket for a model id (chat / image / vision / audio / speech), if listed."""
    pid = normalize_provider_id(provider_id)
    mid = str(model_id or "").strip()
    if not pid or not mid:
        return None
    cmp_ids = {mid}
    if pid == "deepseek":
        cmp_ids.add(normalize_deepseek_chat_model(mid))
    if pid == "volcengine":
        cmp_ids.add(normalize_volcengine_image_model(mid))
    for ut in USE_TYPE_LABELS:
        for m in list_models_for_provider(pid, ut):
            if m["id"] in cmp_ids:
                return ut
    low = mid.lower()
    if pid == "volcengine" and "seedream" in low:
        return "image"
    if pid == "minimax" and low.startswith("image"):
        return "image"
    if pid == "minimax" and low.startswith("speech"):
        return "speech"
    if pid == "minimax" and low.startswith("music"):
        return "music"
    if pid == "minimax" and low.startswith("hailuo"):
        return "video_gen"
    if pid == "agnes" and "video" in low:
        return "video_gen"
    return None


def _default_model_id(spec: Dict[str, Any], use_type: str) -> str:
    models = list_models_for_provider(str(spec.get("id") or ""), use_type)
    if models:
        return models[0]["id"]
    if use_type == "image":
        return str(spec.get("default_image_model") or "").strip()
    if use_type == "speech":
        return str(spec.get("default_speech_model") or "").strip()
    if use_type == "music":
        return str(spec.get("default_music_model") or "").strip()
    if use_type == "video_gen":
        return str(spec.get("default_video_gen_model") or "").strip()
    return str(spec.get("default_chat_model") or "").strip()


def model_label_for_provider(provider_id: str, use_type: str, model_id: str) -> str:
    for m in list_models_for_provider(provider_id, use_type):
        if m["id"] == model_id:
            return m["label"]
    return model_id


def preset_auto_name(provider: str, model: str) -> str:
    """UI/存储用显示名：``provider_id/model_id``（如 ``minimax/image-01``）。"""
    pid = normalize_provider_id(str(provider or "")) or str(provider or "").strip() or "custom"
    mid = str(model or "").strip() or "default"
    return f"{pid}/{mid}"


def preset_auto_id(provider: str, model: str) -> str:
    """由 provider/model 派生的稳定 preset id（新建时用）。"""
    import re

    raw = preset_auto_name(provider, model).replace("/", "_")
    slug = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", raw)
    slug = re.sub(r"_+", "_", slug).strip("_").lower()
    return slug or "preset"


def preset_display_name(preset: Dict[str, Any]) -> str:
    provider = resolve_provider_for_preset(preset)
    model = str(preset.get("model") or "").strip()
    return preset_auto_name(provider, model)


# 方舟模型列表 https://www.volcengine.com/docs/82379/1330310 — 旧简称 → 官方 Model ID
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing
# https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
# 旧名 deepseek-reasoner = v4-flash 思考模式（同一 model id，靠 thinking 开关）
DEEPSEEK_MODEL_ALIASES: Dict[str, str] = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
}

VOLCENGINE_IMAGE_MODEL_ALIASES: Dict[str, str] = {
    "doubao-seedream-5-0-lite": "doubao-seedream-5-0-lite-260128",
    "seedream-5-0-lite": "doubao-seedream-5-0-lite-260128",
    "doubao-seedream-5-0": "doubao-seedream-5-0-260128",
    "seedream-5-0": "doubao-seedream-5-0-260128",
    "doubao-seedream-4-5": "doubao-seedream-4-5-251128",
    "seedream-4-5": "doubao-seedream-4-5-251128",
    "doubao-seedream-4-0": "doubao-seedream-4-0-250828",
    "seedream-4-0": "doubao-seedream-4-0-250828",
    "seedream-4-0-250828": "doubao-seedream-4-0-250828",
}


def normalize_deepseek_chat_model(model: str) -> str:
    mid = str(model or "").strip()
    if not mid:
        return mid
    return DEEPSEEK_MODEL_ALIASES.get(mid.lower(), mid)


def normalize_volcengine_image_model(model: str) -> str:
    mid = str(model or "").strip()
    if not mid:
        return mid
    key = mid.lower()
    return VOLCENGINE_IMAGE_MODEL_ALIASES.get(key, mid)


def _apply_preset_use_type(out: Dict[str, Any], use_type: str) -> None:
    """Write use_type and mutually-exclusive supports_* flags onto preset dict."""
    out["use_type"] = use_type
    out["supports_vision"] = use_type == "vision"
    out["supports_image_gen"] = use_type == "image"
    out["supports_audio"] = use_type == "audio"
    out["supports_speech"] = use_type == "speech"
    out["supports_music"] = use_type == "music"
    out["supports_video_gen"] = use_type == "video_gen"
    if use_type == "chat":
        out["supports_vision"] = False
        out["supports_image_gen"] = False
        out["supports_audio"] = False
        out["supports_speech"] = False
        out["supports_music"] = False
        out["supports_video_gen"] = False
    elif use_type == "speech":
        out["supports_vision"] = False
        out["supports_image_gen"] = False
        out["supports_audio"] = False
        out["supports_speech"] = True
        out["supports_music"] = False
        out["supports_video_gen"] = False
    elif use_type == "music":
        out["supports_vision"] = False
        out["supports_image_gen"] = False
        out["supports_audio"] = False
        out["supports_speech"] = False
        out["supports_music"] = True
        out["supports_video_gen"] = False
    elif use_type == "video_gen":
        out["supports_vision"] = False
        out["supports_image_gen"] = False
        out["supports_audio"] = False
        out["supports_speech"] = False
        out["supports_music"] = False
        out["supports_video_gen"] = True


def _resolve_form_use_type(body: Dict[str, Any], provider: str, model: str) -> str:
    inferred_ut = infer_use_type_for_provider_model(provider, model)
    use_type = (
        inferred_ut
        or str(body.get("use_type") or infer_preset_use_type(body)).strip().lower()
    )
    if use_type not in USE_TYPE_LABELS:
        use_type = "chat"
    return use_type


def materialize_preset_from_form(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    简易表单：provider + use_type + model + api_key (+ name/id)
    → 完整 preset（base_url、supports_* 由目录填充）。
    ``provider=custom`` 或 ``advanced=true`` 时保留手填字段，仍写入 use_type / supports_*。
    """
    out = dict(body)
    provider = normalize_provider_id(str(body.get("provider") or ""))
    if not provider:
        provider = infer_provider_from_url(str(body.get("base_url") or "")) or "openai_compatible"
    advanced = body.get("advanced") in (True, "true", "1", 1)
    if provider == "custom" or advanced:
        out["provider"] = provider or "custom"
        model = str(out.get("model") or "").strip()
        out["model"] = model
        use_type = _resolve_form_use_type(body, out["provider"], model)
        spec = get_provider_spec(out["provider"])
        base_url = str(out.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            base_url = str(spec.get("default_base_url") or "").strip().rstrip("/")
        out["base_url"] = base_url
        raw_auth = str(out.get("auth_scheme") if out.get("auth_scheme") is not None else body.get("auth_scheme") or "Bearer").strip()
        out["auth_scheme"] = raw_auth if raw_auth and raw_auth.lower() != "none" else ""
        _apply_preset_use_type(out, use_type)
        out["name"] = preset_auto_name(out["provider"], model)
        if not str(out.get("id") or "").strip():
            out["id"] = preset_auto_id(out["provider"], model)
        return out

    model = str(body.get("model") or "").strip()
    inferred_ut = infer_use_type_for_provider_model(provider, model)
    use_type = (
        inferred_ut
        or str(body.get("use_type") or infer_preset_use_type(body)).strip().lower()
    )
    if use_type not in USE_TYPE_LABELS:
        use_type = "chat"

    spec = get_provider_spec(provider)
    caps = set(spec.get("capabilities") or [])
    if use_type not in caps and use_type != "chat":
        # 不支持的能力回退到 chat
        use_type = "chat" if "chat" in caps else next(iter(caps), "chat")
    if provider == "deepseek" and use_type == "chat":
        model = normalize_deepseek_chat_model(model)
    if provider == "volcengine" and use_type == "image":
        model = normalize_volcengine_image_model(model)
    allowed = {m["id"] for m in list_models_for_provider(provider, use_type)}
    if allowed and model not in allowed:
        model = _default_model_id(spec, use_type)
    if not model:
        model = _default_model_id(spec, use_type)
    if provider == "volcengine" and use_type == "image":
        model = normalize_volcengine_image_model(model)

    base_url = str(spec.get("default_base_url") or "").strip().rstrip("/")
    if not base_url:
        base_url = str(body.get("base_url") or "").strip().rstrip("/")

    out.update(
        {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "auth_scheme": str(body.get("auth_scheme") or "Bearer").strip() or "Bearer",
        }
    )
    _apply_preset_use_type(out, use_type)
    out["name"] = preset_auto_name(provider, model)
    if not str(out.get("id") or "").strip():
        out["id"] = preset_auto_id(provider, model)
    return out


def normalize_provider_id(raw: Optional[str]) -> str:
    pid = (raw or "").strip().lower().replace(" ", "_")
    if pid in _CATALOG_BY_ID:
        return pid
    # Legacy aliases
    aliases = {
        "deep_seek": "deepseek",
        "openai-compat": "openai_compatible",
        "openai_compat": "openai_compatible",
        "minimax_api": "minimax",
        "ark": "volcengine",
        "volc_engine": "volcengine",
        "doubao": "volcengine",
        "agnes_ai": "agnes",
    }
    return aliases.get(pid, pid if pid in _CATALOG_BY_ID else "")


def infer_provider_from_url(base_url: str) -> str:
    raw = (base_url or "").strip().lower()
    if "api.deepseek.com" in raw:
        return "deepseek"
    if "minimaxi.com" in raw or "minimax.io" in raw:
        return "minimax"
    if "volces.com" in raw or "volcengine.com" in raw:
        return "volcengine"
    if "agnes-ai.com" in raw or "apihub.agnes-ai.com" in raw:
        return "agnes"
    if "api.openai.com" in raw:
        return "openai"
    if "openai.azure.com" in raw or ".azure.com/openai" in raw:
        return "azure_openai"
    if ":11434" in raw or "ollama" in raw:
        return "ollama"
    if any(h in raw for h in ("127.0.0.1", "localhost", "0.0.0.0")):
        if ":30000" in raw or "sglang" in raw:
            return "sglang"
        return "openai_compatible"
    return ""


def resolve_provider_for_preset(preset: Dict[str, Any]) -> str:
    """Effective provider id for a preset dict."""
    explicit = normalize_provider_id(str(preset.get("provider") or ""))
    if explicit and explicit != "custom":
        return explicit
    inferred = infer_provider_from_url(str(preset.get("base_url") or ""))
    if inferred:
        return inferred
    return explicit or "openai_compatible"


def get_provider_spec(provider_id: str) -> Dict[str, Any]:
    pid = normalize_provider_id(provider_id) or "openai_compatible"
    return dict(_CATALOG_BY_ID.get(pid) or _CATALOG_BY_ID["openai_compatible"])


def resolve_chat_protocol(*, provider: str = "", base_url: str = "") -> str:
    pid = normalize_provider_id(provider)
    raw = (base_url or "").lower()
    # Anthropic 协议端点（/anthropic 路径）优先级最高，触发主动缓存分支
    if "/anthropic" in raw or "anthropic." in raw:
        return "minimax_anthropic"
    if pid and pid != "custom":
        return str(get_provider_spec(pid).get("chat_protocol") or "openai")
    inferred = infer_provider_from_url(base_url)
    if inferred:
        return str(get_provider_spec(inferred).get("chat_protocol") or "openai")
    if "api.deepseek.com" in raw:
        return "deepseek"
    return "openai"


def resolve_image_protocol(preset: Dict[str, Any]) -> str:
    if preset.get("supports_image_gen") is not True:
        return "none"
    pid = resolve_provider_for_preset(preset)
    proto = str(get_provider_spec(pid).get("image_protocol") or "none")
    if proto == "none" and preset.get("supports_image_gen") is True:
        # User enabled image gen on a proxy — allow OpenAI images endpoint.
        return "openai_images"
    return proto


def resolve_music_protocol(preset: Dict[str, Any]) -> str:
    if preset.get("supports_music") is not True:
        return "none"
    pid = resolve_provider_for_preset(preset)
    return str(get_provider_spec(pid).get("music_protocol") or "none")


def resolve_video_protocol(preset: Dict[str, Any]) -> str:
    if preset.get("supports_video_gen") is not True:
        return "none"
    pid = resolve_provider_for_preset(preset)
    return str(get_provider_spec(pid).get("video_protocol") or "none")


def uses_deepseek_chat_protocol(*, provider: str = "", base_url: str = "") -> bool:
    return resolve_chat_protocol(provider=provider, base_url=base_url) == "deepseek"


def preset_display_fields(preset: Dict[str, Any]) -> Dict[str, str]:
    """UI-facing provider ids/labels (effective may differ from stored when auto)."""
    stored = normalize_provider_id(str(preset.get("provider") or ""))
    effective = resolve_provider_for_preset(preset)
    spec = get_provider_spec(effective)
    label = str(spec.get("label") or effective or "OpenAI 兼容")
    use_type = infer_preset_use_type(preset)
    model_id = str(preset.get("model") or "")
    return {
        "provider_effective": effective,
        "provider_label": label,
        "provider_stored": stored,
        "use_type": use_type,
        "use_type_label": USE_TYPE_LABELS.get(use_type, use_type),
        "model_label": model_label_for_provider(effective, use_type, model_id) if model_id else "",
    }


def enrich_presets_for_ui(presets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach ``provider_label`` / ``provider_effective`` for Web UI display."""
    out: List[Dict[str, Any]] = []
    for p in presets:
        if not isinstance(p, dict):
            continue
        row = dict(p)
        row.update(preset_display_fields(row))
        row["name"] = preset_display_name(row)
        out.append(row)
    return out


def enrich_preset_defaults(preset: Dict[str, Any]) -> Dict[str, Any]:
    """Attach normalized provider + protocol hints (does not mutate input)."""
    out = dict(preset)
    pid = resolve_provider_for_preset(out)
    out["provider"] = pid
    spec = get_provider_spec(pid)
    out["_chat_protocol"] = resolve_chat_protocol(provider=pid, base_url=str(out.get("base_url") or ""))
    out["_image_protocol"] = resolve_image_protocol(out)
    if not str(out.get("base_url") or "").strip() and spec.get("default_base_url"):
        out.setdefault("base_url", spec["default_base_url"])
    if not str(out.get("model") or "").strip() and spec.get("default_chat_model"):
        out.setdefault("model", spec["default_chat_model"])
    return out


# ---------------------------------------------------------------------------
# Chat: extra request body / behavior flags
# ---------------------------------------------------------------------------


def normalize_reasoning_effort(raw: Optional[str] = None) -> str:
    """Map UI/env reasoning effort to DeepSeek V4 values (high | max).

    Returns the value unchanged when ``chat_protocol=minimax`` since MiniMax-M3
    accepts the full ``minimal | low | medium | high | none`` scale directly.
    """
    effort = (raw or "").strip().lower()
    if not effort:
        from seed.core import env_access as _ea

        effort = _ea.pick_default("high", *_ea.LLM_REASONING_EFFORT).strip().lower()
    if effort in ("low", "medium", "minimal", "high", "max", "none", "xhigh"):
        return "max" if effort == "xhigh" else effort
    return "high"


def apply_chat_thinking_extra_body(
    *,
    chat_protocol: str,
    base_url: str,
    params: Dict[str, Any],
    extra_body: Dict[str, Any],
    resolved_thinking: bool,
    reasoning_effort: Optional[str] = None,
) -> None:
    """Merge provider-specific thinking/reasoning fields into params (in place)."""
    _ = base_url

    if chat_protocol == "deepseek":
        # V4 思考模式：https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
        extra_body["thinking"] = {"type": "enabled" if resolved_thinking else "disabled"}
        if resolved_thinking:
            params["reasoning_effort"] = normalize_reasoning_effort(reasoning_effort)
        return

    if chat_protocol == "sglang":
        if _ea.pick_default("1", *_ea.LLM_SEPARATE_REASONING) != "0":
            extra_body["separate_reasoning"] = True
        if _ea.pick_default("1", *_ea.LLM_CHAT_TEMPLATE_KWARGS) != "0":
            extra_body.setdefault("chat_template_kwargs", {})
            extra_body["chat_template_kwargs"]["enable_thinking"] = resolved_thinking
        return

    if chat_protocol == "minimax":
        # MiniMax-M3 原生 Interleaved Thinking：reasoning_split=True 让思考内容
        # 通过 reasoning_details 字段单独返回，便于在多轮 Function Call 中完整回传。
        # 文档: https://platform.minimaxi.com/docs/guides/text-m3-function-call
        # M2.x 系列使用 <think>...</think> 内联在 content；llm_exec 负责剥离。
        if resolved_thinking:
            extra_body.setdefault("reasoning_split", True)
        return

    # openai / azure / ollama / compatible: optional user extra_body only


def should_send_reasoning_content(*, chat_protocol: str, base_url: str) -> bool:
    from seed.core import env_access as _ea

    env = _ea.pick_default("", *_ea.LLM_SEND_REASONING_CONTENT).strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return chat_protocol == "deepseek" or uses_deepseek_chat_protocol(
        provider="", base_url=base_url
    )


def default_max_request_body_bytes(chat_protocol: str, base_url: str) -> int:
    from seed.core import env_access as _ea

    raw = _ea.pick_nonempty(*_ea.LLM_MAX_REQUEST_BODY_BYTES)
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("Invalid SEED_LLM_MAX_REQUEST_BODY_BYTES=%r", raw)
    if chat_protocol == "deepseek" or uses_deepseek_chat_protocol(provider="", base_url=base_url):
        return 786432
    return 0


# ---------------------------------------------------------------------------
# Image generation adapters
# ---------------------------------------------------------------------------

_ALLOWED_IMAGE_SIZES = frozenset(
    {
        "256x256",
        "512x512",
        "1024x1024",
        "1024x1792",
        "1792x1024",
        "1536x1024",
        "1024x1536",
    }
)

# MiniMax aspect_ratio values (see platform.minimaxi.com image-generation API)
_MINIMAX_ASPECT_RATIOS = frozenset(
    {"1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"}
)

_SIZE_TO_MINIMAX_ASPECT: Dict[str, str] = {
    "1024x1024": "1:1",
    "256x256": "1:1",
    "512x512": "1:1",
    "1792x1024": "16:9",
    "1024x1792": "9:16",
    "1536x1024": "3:2",
    "1024x1536": "2:3",
}

_VOLCENGINE_SIZE_KEYWORDS = frozenset({"1K", "2K", "3K", "4K"})
_SIZE_TO_VOLCENGINE: Dict[str, str] = {
    "1024x1024": "2K",
    "256x256": "2K",
    "512x512": "2K",
    "1792x1024": "2K",
    "1024x1792": "2K",
    "1536x1024": "2K",
    "1024x1536": "2K",
}


def _images_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/images/generations"):
        return base
    return f"{base}/images/generations"


def _auth_headers(preset: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = str(preset.get("api_key") or "").strip()
    scheme = str(preset.get("auth_scheme") or "Bearer").strip() or "Bearer"
    if key:
        headers["Authorization"] = f"{scheme} {key}"
    return headers


def _decode_image_item(item: dict[str, Any]) -> bytes:
    b64 = item.get("b64_json")
    if b64:
        return base64.standard_b64decode(str(b64))
    url = str(item.get("url") or "").strip()
    if url:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        return resp.content
    raise ValueError("image item missing b64_json and url")


# Seedream 5.0 lite 等要求至少约 3.69M 像素（见方舟 API 报错）
_VOLCENGINE_MIN_PIXELS = 3_686_400


def size_to_volcengine_size(size: str, default: str = "2K") -> str:
    """Map tool ``size`` to Seedream ``size`` (2K/3K/4K or WxH)."""
    raw = (size or default).strip()
    mapped = _SIZE_TO_VOLCENGINE.get(raw)
    if mapped:
        return mapped
    upper = raw.upper()
    if upper in _VOLCENGINE_SIZE_KEYWORDS:
        return upper
    if "x" in raw.lower() and raw[0].isdigit():
        parts = raw.lower().split("x", 1)
        try:
            w, h = int(parts[0]), int(parts[1])
            if w > 0 and h > 0 and w * h < _VOLCENGINE_MIN_PIXELS:
                return "2048x2048"
        except ValueError:
            pass
        return raw
    return default if default.upper() in _VOLCENGINE_SIZE_KEYWORDS else "2K"


def call_openai_images_generations(
    preset: dict[str, Any],
    *,
    prompt: str,
    size: str,
    n: int,
    quality: str = "",
    reference_images: Optional[List[str]] = None,
) -> List[bytes]:
    model = str(preset.get("model") or "").strip()
    if not model:
        raise ValueError("image_gen preset missing model")
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
    }
    q = (quality or "").strip().lower()
    if q in ("standard", "hd"):
        payload["quality"] = q
    payload["response_format"] = "b64_json"
    _ = reference_images  # OpenAI DALL·E generations: no standard image input on this path

    url = _images_url(str(preset.get("base_url") or ""))
    headers = _auth_headers(preset)
    timeout = int(os.environ.get("CODEAGENT_IMAGE_GEN_TIMEOUT_SEC", "180") or 180)

    resp = requests.post(url, json=payload, headers=headers, timeout=max(30, timeout))
    if resp.status_code >= 400 and payload.get("response_format"):
        payload.pop("response_format", None)
        resp = requests.post(url, json=payload, headers=headers, timeout=max(30, timeout))
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            err = resp.json().get("error", detail)
            if isinstance(err, dict):
                detail = err.get("message") or str(err)
            else:
                detail = err
        except Exception:
            pass
        raise ValueError(f"image generation failed ({resp.status_code}): {detail}")

    data = resp.json()
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError("image generation returned no data")
    out: List[bytes] = []
    for item in items:
        if isinstance(item, dict):
            out.append(_decode_image_item(item))
    if not out:
        raise ValueError("failed to decode generated images")
    return out


def _minimax_image_url(base_url: str) -> str:
    """Resolve MiniMax image_generation endpoint from preset base_url."""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/image_generation"):
        return base
    if base.endswith("/v1"):
        return f"{base}/image_generation"
    return f"{base}/v1/image_generation"


def size_to_minimax_aspect_ratio(size: str, default: str = "1:1") -> str:
    """Map OpenAI-style ``WxH`` or pass through MiniMax ``aspect_ratio``."""
    raw = (size or default).strip()
    if raw in _MINIMAX_ASPECT_RATIOS:
        return raw
    return _SIZE_TO_MINIMAX_ASPECT.get(raw, default if default in _MINIMAX_ASPECT_RATIOS else "1:1")


def call_minimax_image_generation(
    preset: dict[str, Any],
    *,
    prompt: str,
    size: str,
    n: int,
    quality: str = "",
    reference_images: Optional[List[str]] = None,
) -> List[bytes]:
    """
    MiniMax 文生图 / 图生图: POST /v1/image_generation
    Docs: https://platform.minimaxi.com/docs/guides/image-generation
    """
    model = str(preset.get("model") or "").strip() or "image-01"
    aspect = size_to_minimax_aspect_ratio(size, "1:1")
    count = max(1, min(int(n), 9))

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect,
        "n": count,
        "response_format": "base64",
    }
    refs = [str(u).strip() for u in (reference_images or []) if str(u).strip()]
    if refs:
        payload["subject_reference"] = [
            {"type": "character", "image_file": ref} for ref in refs[:4]
        ]
    _ = quality

    url = _minimax_image_url(str(preset.get("base_url") or "https://api.minimaxi.com/v1"))
    headers = _auth_headers(preset)
    timeout = int(os.environ.get("CODEAGENT_IMAGE_GEN_TIMEOUT_SEC", "180") or 180)

    resp = requests.post(url, json=payload, headers=headers, timeout=max(30, timeout))
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            body = resp.json()
            br = body.get("base_resp") if isinstance(body, dict) else None
            if isinstance(br, dict) and br.get("status_msg"):
                detail = str(br.get("status_msg"))
        except Exception:
            pass
        raise ValueError(f"MiniMax image_generation failed ({resp.status_code}): {detail}")

    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("MiniMax image_generation returned invalid JSON")

    base_resp = body.get("base_resp")
    if isinstance(base_resp, dict):
        code = base_resp.get("status_code", 0)
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = 0
        if code != 0:
            msg = base_resp.get("status_msg") or f"status_code={code}"
            raise ValueError(f"MiniMax image_generation error: {msg}")

    data = body.get("data")
    if not isinstance(data, dict):
        raise ValueError("MiniMax image_generation returned no data")

    out: List[bytes] = []
    b64_list = data.get("image_base64")
    if isinstance(b64_list, list):
        for item in b64_list:
            if item:
                out.append(base64.standard_b64decode(str(item)))

    if not out:
        urls = data.get("image_urls")
        if isinstance(urls, list):
            for u in urls:
                u = str(u or "").strip()
                if not u:
                    continue
                r = requests.get(u, timeout=120)
                r.raise_for_status()
                out.append(r.content)

    if not out:
        raise ValueError("MiniMax image_generation returned no images")
    return out[:count]


def call_volcengine_image_generation(
    preset: dict[str, Any],
    *,
    prompt: str,
    size: str,
    n: int,
    quality: str = "",
    reference_images: Optional[List[str]] = None,
) -> List[bytes]:
    """
    火山方舟 Seedream: POST /api/v3/images/generations
    Docs: https://www.volcengine.com/docs/82379/1541523
    """
    model = normalize_volcengine_image_model(str(preset.get("model") or "").strip())
    if not model:
        raise ValueError("image_gen preset missing model (火山方舟 Model ID，见文档 1330310)")
    ve_size = size_to_volcengine_size(size, "2K")
    count = max(1, min(int(n), 15))

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": ve_size,
        "response_format": "b64_json",
        "watermark": False,
        "sequential_image_generation": "disabled",
    }
    if count > 1:
        payload["sequential_image_generation"] = "auto"
        payload["sequential_image_generation_options"] = {"max_images": count}
    if (quality or "").strip().lower() == "hd":
        payload["optimize_prompt_options"] = {"mode": "standard"}

    refs = [str(u).strip() for u in (reference_images or []) if str(u).strip()]
    if refs:
        payload["image"] = refs[0] if len(refs) == 1 else refs[:10]

    url = _images_url(str(preset.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"))
    headers = _auth_headers(preset)
    timeout = int(os.environ.get("CODEAGENT_IMAGE_GEN_TIMEOUT_SEC", "180") or 180)

    resp = requests.post(url, json=payload, headers=headers, timeout=max(30, timeout))
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            err = resp.json().get("error", detail)
            if isinstance(err, dict):
                detail = err.get("message") or str(err)
            else:
                detail = err
        except Exception:
            pass
        raise ValueError(f"Volcengine image generation failed ({resp.status_code}): {detail}")

    data = resp.json()
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError("Volcengine image generation returned no data")
    out: List[bytes] = []
    for item in items:
        if isinstance(item, dict):
            out.append(_decode_image_item(item))
    if not out:
        raise ValueError("failed to decode Volcengine generated images")
    return out[:count]


def call_image_generations(
    preset: dict[str, Any],
    *,
    prompt: str,
    size: str,
    n: int,
    quality: str = "",
    reference_images: Optional[List[str]] = None,
) -> List[bytes]:
    """Dispatch image generation by preset provider / protocol."""
    proto = resolve_image_protocol(preset)
    pid = resolve_provider_for_preset(preset)
    kwargs = {
        "prompt": prompt,
        "size": size,
        "n": n,
        "quality": quality,
        "reference_images": reference_images,
    }
    if proto == "openai_images":
        return call_openai_images_generations(preset, **kwargs)
    if proto == "minimax_image":
        return call_minimax_image_generation(preset, **kwargs)
    if proto == "volcengine_images":
        return call_volcengine_image_generation(preset, **kwargs)
    raise ValueError(
        f"provider {pid!r} does not support image_generate (image_protocol={proto!r}). "
        "See codeagent/docs/IMAGE_GEN_PROVIDERS.md"
    )


def _minimax_music_url(base_url: str) -> str:
    """Resolve MiniMax music_generation endpoint from preset base_url."""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/music_generation"):
        return base
    if base.endswith("/v1"):
        return f"{base}/music_generation"
    return f"{base}/v1/music_generation"


def _minimax_video_url(base_url: str) -> str:
    """Resolve MiniMax video_generation endpoint from preset base_url."""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/video_generation"):
        return base
    if base.endswith("/v1"):
        return f"{base}/video_generation"
    return f"{base}/v1/video_generation"


def call_minimax_music_generation(
    preset: dict[str, Any],
    *,
    prompt: str,
    lyrics: str = "",
    is_instrumental: bool = False,
    lyrics_optimizer: bool = False,
    audio_url: str = "",
    audio_base64: str = "",
    cover_feature_id: str = "",
) -> tuple[bytes, str, dict[str, Any]]:
    """
    MiniMax 音乐生成: POST /v1/music_generation
    Docs: https://platform.minimaxi.com/docs/guides/music-generation
    """
    model = str(preset.get("model") or "").strip() or "music-2.6"
    low_model = model.lower()
    is_cover = "cover" in low_model

    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "output_format": "hex",
        "audio_setting": {
            "sample_rate": 44100,
            "bitrate": 256000,
            "format": "mp3",
        },
    }

    style = (prompt or "").strip()
    song_lyrics = (lyrics or "").strip()
    if style:
        payload["prompt"] = style
    if song_lyrics:
        payload["lyrics"] = song_lyrics
    if is_instrumental:
        payload["is_instrumental"] = True
    if lyrics_optimizer:
        payload["lyrics_optimizer"] = True

    ref_url = (audio_url or "").strip()
    ref_b64 = (audio_base64 or "").strip()
    feature_id = (cover_feature_id or "").strip()
    if feature_id:
        payload["cover_feature_id"] = feature_id
    elif ref_b64:
        payload["audio_base64"] = ref_b64
    elif ref_url:
        payload["audio_url"] = ref_url

    if is_cover and not (feature_id or ref_b64 or ref_url):
        raise ValueError("music-cover models require audio_url, audio_base64, or cover_feature_id")
    if not is_cover and not is_instrumental and not lyrics_optimizer and not song_lyrics:
        raise ValueError("lyrics required (or set lyrics_optimizer=true or is_instrumental=true)")

    url = _minimax_music_url(str(preset.get("base_url") or "https://api.minimaxi.com/v1"))
    headers = _auth_headers(preset)
    timeout = int(os.environ.get("CODEAGENT_MUSIC_GEN_TIMEOUT_SEC", "300") or 300)

    resp = requests.post(url, json=payload, headers=headers, timeout=max(60, timeout))
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            body = resp.json()
            br = body.get("base_resp") if isinstance(body, dict) else None
            if isinstance(br, dict) and br.get("status_msg"):
                detail = str(br.get("status_msg"))
        except Exception:
            pass
        raise ValueError(f"MiniMax music_generation failed ({resp.status_code}): {detail}")

    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("MiniMax music_generation returned invalid JSON")

    base_resp = body.get("base_resp")
    if isinstance(base_resp, dict):
        code = base_resp.get("status_code", 0)
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = 0
        if code != 0:
            msg = base_resp.get("status_msg") or f"status_code={code}"
            raise ValueError(f"MiniMax music_generation error: {msg}")

    data = body.get("data")
    if not isinstance(data, dict):
        raise ValueError("MiniMax music_generation returned no data")

    status = data.get("status")
    try:
        if status is not None and int(status) != 2:
            raise ValueError(f"MiniMax music_generation incomplete (status={status})")
    except ValueError:
        raise
    except (TypeError, ValueError):
        pass

    raw_audio = data.get("audio")
    if not raw_audio:
        raise ValueError("MiniMax music_generation returned no audio")

    audio_str = str(raw_audio).strip()
    mime = "audio/mpeg"
    fmt = str((payload.get("audio_setting") or {}).get("format") or "mp3").lower()
    if fmt == "wav":
        mime = "audio/wav"
    elif fmt == "pcm":
        mime = "audio/pcm"

    if audio_str.startswith("http://") or audio_str.startswith("https://"):
        r = requests.get(audio_str, timeout=max(60, timeout))
        r.raise_for_status()
        audio_bytes = r.content
    else:
        try:
            audio_bytes = bytes.fromhex(audio_str)
        except ValueError as e:
            raise ValueError("MiniMax music_generation returned invalid hex audio") from e

    if not audio_bytes:
        raise ValueError("MiniMax music_generation returned empty audio")

    meta: dict[str, Any] = {}
    extra = body.get("extra_info")
    if isinstance(extra, dict):
        for k in ("music_duration", "music_sample_rate", "music_channel", "bitrate", "music_size"):
            if extra.get(k) is not None:
                meta[k] = extra.get(k)

    return audio_bytes, mime, meta


def call_music_generations(
    preset: dict[str, Any],
    *,
    prompt: str,
    lyrics: str = "",
    is_instrumental: bool = False,
    lyrics_optimizer: bool = False,
    audio_url: str = "",
    audio_base64: str = "",
    cover_feature_id: str = "",
) -> tuple[bytes, str, dict[str, Any]]:
    """Dispatch music generation by preset provider / protocol."""
    proto = resolve_music_protocol(preset)
    pid = resolve_provider_for_preset(preset)
    kwargs = {
        "prompt": prompt,
        "lyrics": lyrics,
        "is_instrumental": is_instrumental,
        "lyrics_optimizer": lyrics_optimizer,
        "audio_url": audio_url,
        "audio_base64": audio_base64,
        "cover_feature_id": cover_feature_id,
    }
    if proto == "minimax_music":
        return call_minimax_music_generation(preset, **kwargs)
    raise ValueError(
        f"provider {pid!r} does not support music_generate (music_protocol={proto!r}). "
        "Configure a MiniMax music preset (supports_music)."
    )


def _agnes_videos_url(base_url: str) -> str:
    """Resolve Agnes video task endpoint from preset base_url."""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/videos"):
        return base
    return f"{base}/videos"


def normalize_video_num_frames(num_frames: Optional[int] = None, default: int = 121) -> int:
    """Snap frame count to Agnes rule: <=441 and 8n+1."""
    try:
        n = int(num_frames) if num_frames is not None else int(default)
    except (TypeError, ValueError):
        n = int(default)
    n = max(9, min(441, n))
    return ((n - 1) // 8) * 8 + 1


def call_agnes_video_generation(
    preset: dict[str, Any],
    *,
    prompt: str,
    image_url: str = "",
    image_urls: Optional[List[str]] = None,
    mode: str = "",
    height: int = 768,
    width: int = 1152,
    num_frames: int = 121,
    frame_rate: float = 24,
    num_inference_steps: Optional[int] = None,
    seed: Optional[int] = None,
    negative_prompt: str = "",
) -> tuple[bytes, str, dict[str, Any]]:
    """
    Agnes video generation: POST /v1/videos then poll GET /v1/videos/{task_id}.
    Docs: https://agnes-ai.com/doc/agnes-video-v20
    """
    model = str(preset.get("model") or "").strip() or "agnes-video-v2.0"
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt required")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": text,
        "height": int(height),
        "width": int(width),
        "num_frames": normalize_video_num_frames(num_frames),
        "frame_rate": float(frame_rate),
    }
    neg = (negative_prompt or "").strip()
    if neg:
        payload["negative_prompt"] = neg
    if num_inference_steps is not None:
        payload["num_inference_steps"] = int(num_inference_steps)
    if seed is not None:
        payload["seed"] = int(seed)

    refs = [str(u).strip() for u in (image_urls or []) if str(u).strip()]
    single = (image_url or "").strip()
    gen_mode = (mode or "").strip().lower()
    extra_body: dict[str, Any] = {}

    if len(refs) >= 2 or gen_mode == "keyframes":
        imgs = refs if refs else ([single] if single else [])
        if not imgs:
            raise ValueError("keyframes mode requires image_urls or image_url")
        extra_body["image"] = imgs
        extra_body["mode"] = gen_mode or "keyframes"
    elif len(refs) == 1 and not single:
        payload["image"] = refs[0]
    elif single and not refs:
        payload["image"] = single
    elif refs:
        extra_body["image"] = refs

    if extra_body:
        payload["extra_body"] = extra_body
        if gen_mode and gen_mode != "keyframes" and "mode" not in extra_body:
            extra_body["mode"] = gen_mode

    base_url = str(preset.get("base_url") or "https://apihub.agnes-ai.com/v1")
    create_url = _agnes_videos_url(base_url)
    headers = _auth_headers(preset)
    timeout = int(os.environ.get("CODEAGENT_VIDEO_GEN_TIMEOUT_SEC", "600") or 600)
    poll_interval = float(os.environ.get("CODEAGENT_VIDEO_GEN_POLL_INTERVAL_SEC", "5") or 5)

    resp = requests.post(create_url, json=payload, headers=headers, timeout=max(60, timeout))
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("error"):
                detail = str(body.get("error"))
        except Exception:
            pass
        raise ValueError(f"Agnes video create failed ({resp.status_code}): {detail}")

    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("Agnes video create returned invalid JSON")
    task_id = str(body.get("id") or "").strip()
    if not task_id:
        raise ValueError("Agnes video create returned no task id")

    deadline = time.monotonic() + max(60, timeout)
    status_url = f"{create_url.rstrip('/')}/{task_id}"
    last_status = str(body.get("status") or "queued")
    result_body: dict[str, Any] = body

    while time.monotonic() < deadline:
        last_status = str(result_body.get("status") or last_status)
        if last_status == "completed":
            video_url = str(result_body.get("video_url") or "").strip()
            if not video_url:
                raise ValueError("Agnes video completed but video_url missing")
            dl = requests.get(video_url, timeout=max(60, timeout))
            dl.raise_for_status()
            video_bytes = dl.content
            if not video_bytes:
                raise ValueError("Agnes video download returned empty body")
            mime = "video/mp4"
            meta: dict[str, Any] = {
                "task_id": task_id,
                "status": last_status,
                "size": result_body.get("size"),
                "seconds": result_body.get("seconds"),
            }
            usage = result_body.get("usage")
            if isinstance(usage, dict):
                meta["usage"] = usage
            return video_bytes, mime, meta
        if last_status == "failed":
            raise ValueError(f"Agnes video generation failed (task_id={task_id})")

        time.sleep(max(1.0, poll_interval))
        poll = requests.get(status_url, headers=headers, timeout=max(60, timeout))
        if poll.status_code >= 400:
            detail = poll.text[:500]
            raise ValueError(f"Agnes video poll failed ({poll.status_code}): {detail}")
        polled = poll.json()
        if not isinstance(polled, dict):
            raise ValueError("Agnes video poll returned invalid JSON")
        result_body = polled
        last_status = str(result_body.get("status") or last_status)

    raise ValueError(
        f"Agnes video generation timed out after {timeout}s (task_id={task_id}, status={last_status})"
    )


def call_minimax_video_generation(
    preset: dict[str, Any],
    *,
    prompt: str,
    image_url: str = "",
    image_urls: Optional[List[str]] = None,
    mode: str = "",
    height: int = 768,
    width: int = 768,
    num_frames: int = 121,
    frame_rate: float = 24,
    num_inference_steps: Optional[int] = None,
    seed: Optional[int] = None,
    negative_prompt: str = "",
) -> tuple[bytes, str, dict[str, Any]]:
    """
    MiniMax Hailuo 视频生成: POST /v1/video_generation then poll GET /v1/video_generation/{task_id}
    Docs: https://platform.minimaxi.com/docs/guides/video-generation
    支持模型: hailuo-2.3 / hailuo-2.3-fast / hailuo-02
    """
    model = str(preset.get("model") or "").strip() or "hailuo-2.3"
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt required")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": text,
    }

    neg = (negative_prompt or "").strip()
    if neg:
        payload["negative_prompt"] = neg
    if num_inference_steps is not None:
        payload["num_inference_steps"] = int(num_inference_steps)
    if seed is not None:
        payload["seed"] = int(seed)

    refs = [str(u).strip() for u in (image_urls or []) if str(u).strip()]
    single = (image_url or "").strip()
    gen_mode = (mode or "").strip().lower()

    if len(refs) >= 2 or gen_mode == "keyframes":
        imgs = refs if refs else ([single] if single else [])
        if not imgs:
            raise ValueError("keyframes mode requires image_urls or image_url")
        payload["image_urls"] = imgs
        payload["mode"] = gen_mode or "keyframes"
    elif len(refs) == 1:
        payload["first_frame_image"] = refs[0]
    elif single:
        payload["first_frame_image"] = single

    base_url = str(preset.get("base_url") or "https://api.minimaxi.com/v1")
    create_url = _minimax_video_url(base_url)
    headers = _auth_headers(preset)
    timeout = int(os.environ.get("CODEAGENT_VIDEO_GEN_TIMEOUT_SEC", "600") or 600)
    poll_interval = float(os.environ.get("CODEAGENT_VIDEO_GEN_POLL_INTERVAL_SEC", "5") or 5)

    resp = requests.post(create_url, json=payload, headers=headers, timeout=max(60, timeout))
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            body = resp.json()
            if isinstance(body, dict):
                br = body.get("base_resp")
                if isinstance(br, dict) and br.get("status_msg"):
                    detail = str(br.get("status_msg"))
                elif body.get("error"):
                    detail = str(body.get("error"))
        except Exception:
            pass
        raise ValueError(f"MiniMax video create failed ({resp.status_code}): {detail}")

    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("MiniMax video create returned invalid JSON")
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("MiniMax video create returned no task_id")

    deadline = time.monotonic() + max(60, timeout)
    status_url = f"{create_url.rstrip('/').rstrip('/v1').rstrip('/video_generation')}/v1/video_generation/{task_id}"
    if not status_url.startswith("http"):
        status_url = f"https://api.minimaxi.com/v1/video_generation/{task_id}"

    last_status = str(body.get("status") or "pending")
    result_body: dict[str, Any] = body

    while time.monotonic() < deadline:
        last_status = str(result_body.get("status") or last_status)
        if last_status == "success":
            video_url = str(
                result_body.get("data", {}).get("video_url")
                if isinstance(result_body.get("data"), dict)
                else result_body.get("video_url")
                or ""
            ).strip()
            if not video_url:
                raise ValueError("MiniMax video completed but video_url missing")
            dl = requests.get(video_url, timeout=max(60, timeout))
            dl.raise_for_status()
            video_bytes = dl.content
            if not video_bytes:
                raise ValueError("MiniMax video download returned empty body")
            mime = "video/mp4"
            meta: dict[str, Any] = {
                "task_id": task_id,
                "status": last_status,
                "model": model,
            }
            return video_bytes, mime, meta
        if last_status in ("fail", "failed", "error"):
            raise ValueError(f"MiniMax video generation failed (task_id={task_id})")

        time.sleep(max(1.0, poll_interval))
        poll = requests.get(status_url, headers=headers, timeout=max(60, timeout))
        if poll.status_code >= 400:
            raise ValueError(f"MiniMax video poll failed ({poll.status_code})")
        polled = poll.json()
        if not isinstance(polled, dict):
            raise ValueError("MiniMax video poll returned invalid JSON")
        result_body = polled
        last_status = str(result_body.get("status") or last_status)

    raise ValueError(
        f"MiniMax video generation timed out after {timeout}s (task_id={task_id}, status={last_status})"
    )


def call_video_generations(
    preset: dict[str, Any],
    *,
    prompt: str,
    image_url: str = "",
    image_urls: Optional[List[str]] = None,
    mode: str = "",
    height: int = 768,
    width: int = 1152,
    num_frames: int = 121,
    frame_rate: float = 24,
    num_inference_steps: Optional[int] = None,
    seed: Optional[int] = None,
    negative_prompt: str = "",
) -> tuple[bytes, str, dict[str, Any]]:
    """Dispatch video generation by preset provider / protocol."""
    proto = resolve_video_protocol(preset)
    pid = resolve_provider_for_preset(preset)
    kwargs = {
        "prompt": prompt,
        "image_url": image_url,
        "image_urls": image_urls,
        "mode": mode,
        "height": height,
        "width": width,
        "num_frames": num_frames,
        "frame_rate": frame_rate,
        "num_inference_steps": num_inference_steps,
        "seed": seed,
        "negative_prompt": negative_prompt,
    }
    if proto == "agnes_video":
        return call_agnes_video_generation(preset, **kwargs)
    if proto == "minimax_video":
        return call_minimax_video_generation(preset, **kwargs)
    raise ValueError(
        f"provider {pid!r} does not support video_generate (video_protocol={proto!r}). "
        "Configure an Agnes or MiniMax video preset (supports_video_gen)."
    )


def normalize_image_size(size: str, default: str, preset: Optional[dict[str, Any]] = None) -> str:
    sz = (size or default).strip()
    if preset:
        proto = resolve_image_protocol(preset)
        if proto == "minimax_image":
            return size_to_minimax_aspect_ratio(sz, size_to_minimax_aspect_ratio(default, "1:1"))
        if proto == "volcengine_images":
            return size_to_volcengine_size(sz, size_to_volcengine_size(default, "2K"))
    if sz in _ALLOWED_IMAGE_SIZES:
        return sz
    return default
