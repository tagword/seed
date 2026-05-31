"""Model provider catalog and protocol resolution."""

from __future__ import annotations

from seed.core.model_providers import (
    infer_provider_from_url,
    preset_display_fields,
    resolve_chat_protocol,
    resolve_image_protocol,
    resolve_provider_for_preset,
    size_to_minimax_aspect_ratio,
    size_to_volcengine_size,
    uses_deepseek_chat_protocol,
    _minimax_image_url,
)


def test_infer_deepseek_url() -> None:
    assert infer_provider_from_url("https://api.deepseek.com/v1") == "deepseek"


def test_resolve_provider_explicit() -> None:
    p = {"provider": "openai", "base_url": "https://api.deepseek.com/v1"}
    assert resolve_provider_for_preset(p) == "openai"


def test_deepseek_chat_protocol() -> None:
    assert resolve_chat_protocol(provider="deepseek", base_url="") == "deepseek"
    assert uses_deepseek_chat_protocol(provider="deepseek", base_url="")


def test_openai_compatible_not_deepseek() -> None:
    assert (
        uses_deepseek_chat_protocol(
            provider="openai_compatible",
            base_url="https://api.deepseek.com/v1",
        )
        is False
    )


def test_image_protocol_openai() -> None:
    p = {"provider": "openai", "supports_image_gen": True, "base_url": "https://api.openai.com/v1"}
    assert resolve_image_protocol(p) == "openai_images"


def test_image_protocol_deepseek_default_none_but_proxy_ok() -> None:
    p = {"provider": "deepseek", "supports_image_gen": True}
    assert resolve_image_protocol(p) == "openai_images"


def test_infer_minimax_url() -> None:
    assert infer_provider_from_url("https://api.minimaxi.com/v1") == "minimax"


def test_minimax_image_protocol() -> None:
    p = {
        "provider": "minimax",
        "supports_image_gen": True,
        "base_url": "https://api.minimaxi.com/v1",
        "model": "image-01",
    }
    assert resolve_image_protocol(p) == "minimax_image"


def test_size_to_minimax_aspect() -> None:
    assert size_to_minimax_aspect_ratio("1024x1024", "1:1") == "1:1"
    assert size_to_minimax_aspect_ratio("1024x1792", "1:1") == "9:16"
    assert size_to_minimax_aspect_ratio("16:9", "1:1") == "16:9"


def test_minimax_image_url() -> None:
    assert (
        _minimax_image_url("https://api.minimaxi.com/v1")
        == "https://api.minimaxi.com/v1/image_generation"
    )


def test_infer_volcengine_url() -> None:
    assert infer_provider_from_url("https://ark.cn-beijing.volces.com/api/v3") == "volcengine"


def test_volcengine_image_protocol() -> None:
    p = {
        "provider": "volcengine",
        "supports_image_gen": True,
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seedream-5-0-lite",
    }
    assert resolve_image_protocol(p) == "volcengine_images"


def test_size_to_volcengine() -> None:
    assert size_to_volcengine_size("1024x1024", "2K") == "2K"
    assert size_to_volcengine_size("1792x1792", "2K") == "2048x2048"
    assert size_to_volcengine_size("2k", "2K") == "2K"
    assert size_to_volcengine_size("2048x2048", "2K") == "2048x2048"


def test_preset_display_fields_infers_label() -> None:
    p = {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}
    fields = preset_display_fields(p)
    assert fields["provider_effective"] == "deepseek"
    assert fields["provider_label"] == "DeepSeek"
    assert fields["provider_stored"] == ""


def test_infer_use_type_for_provider_model() -> None:
    from seed.core.model_providers import infer_preset_use_type, infer_use_type_for_provider_model

    assert infer_use_type_for_provider_model("minimax", "image-01") == "image"
    assert infer_use_type_for_provider_model("minimax", "speech-2.8-turbo") == "speech"
    assert infer_use_type_for_provider_model("minimax", "music-2.6") == "music"
    assert infer_use_type_for_provider_model("minimax", "MiniMax-M2.7") == "chat"
    assert infer_use_type_for_provider_model("deepseek", "deepseek-v4-flash") == "chat"
    assert (
        infer_use_type_for_provider_model("volcengine", "doubao-seedream-5-0-lite-260128")
        == "image"
    )
    assert infer_preset_use_type({"supports_speech": True}) == "speech"
    assert infer_preset_use_type({"supports_music": True}) == "music"


def test_materialize_preset_from_form_music() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    out = materialize_preset_from_form(
        {
            "provider": "minimax",
            "use_type": "music",
            "model": "music-2.6",
            "api_key": "sk-test",
        }
    )
    assert out["use_type"] == "music"
    assert out["supports_music"] is True
    assert out["supports_speech"] is False
    assert out["model"] == "music-2.6"


def test_minimax_music_url() -> None:
    from seed.core.model_providers import _minimax_music_url

    assert (
        _minimax_music_url("https://api.minimaxi.com/v1")
        == "https://api.minimaxi.com/v1/music_generation"
    )


def test_materialize_preset_from_form_speech() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    out = materialize_preset_from_form(
        {
            "provider": "minimax",
            "use_type": "speech",
            "model": "speech-2.8-turbo",
            "api_key": "sk-test",
        }
    )
    assert out["use_type"] == "speech"
    assert out["supports_speech"] is True
    assert out["supports_vision"] is False
    assert out["model"] == "speech-2.8-turbo"


def test_materialize_infers_use_type_from_model() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    out = materialize_preset_from_form(
        {
            "provider": "minimax",
            "model": "image-01",
            "api_key": "sk-test",
        }
    )
    assert out["use_type"] == "image"
    assert out["supports_image_gen"] is True


def test_materialize_preset_from_form_minimax_image() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    body = {
        "id": "minimax-img",
        "name": "MiniMax 生图",
        "provider": "minimax",
        "use_type": "image",
        "model": "image-01",
        "api_key": "sk-test",
    }
    out = materialize_preset_from_form(body)
    assert out["base_url"] == "https://api.minimaxi.com/v1"
    assert out["model"] == "image-01"
    assert out["supports_image_gen"] is True
    assert out["supports_vision"] is False
    assert out.get("use_type") == "image"
    assert out["name"] == "minimax/image-01"


def test_materialize_advanced_vision_flags() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    out = materialize_preset_from_form(
        {
            "provider": "openai_compatible",
            "advanced": True,
            "use_type": "vision",
            "base_url": "http://127.0.0.1:8000/v1",
            "model": "llava",
            "api_key": "",
            "auth_scheme": "",
        }
    )
    assert out["use_type"] == "vision"
    assert out["supports_vision"] is True
    assert out["supports_image_gen"] is False
    assert out["base_url"] == "http://127.0.0.1:8000/v1"


def test_materialize_ollama_chat_defaults() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    out = materialize_preset_from_form(
        {
            "provider": "ollama",
            "advanced": True,
            "use_type": "chat",
            "model": "qwen2.5:7b",
            "api_key": "",
        }
    )
    assert out["use_type"] == "chat"
    assert out["supports_vision"] is False
    assert out["base_url"] == "http://127.0.0.1:11434/v1"


def test_materialize_custom_image_flags() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    out = materialize_preset_from_form(
        {
            "provider": "custom",
            "use_type": "image",
            "base_url": "http://127.0.0.1:7860/v1",
            "model": "sdxl",
            "api_key": "k",
        }
    )
    assert out["use_type"] == "image"
    assert out["supports_image_gen"] is True
    assert out["provider"] == "custom"


def test_normalize_volcengine_image_model() -> None:
    from seed.core.model_providers import normalize_volcengine_image_model

    assert (
        normalize_volcengine_image_model("doubao-seedream-5-0-lite")
        == "doubao-seedream-5-0-lite-260128"
    )
    assert normalize_volcengine_image_model("doubao-seedream-4-0-250828") == "doubao-seedream-4-0-250828"


def test_preset_auto_name() -> None:
    from seed.core.model_providers import preset_auto_name, preset_auto_id

    assert preset_auto_name("minimax", "image-01") == "minimax/image-01"
    assert preset_auto_id("minimax", "image-01") == "minimax_image-01"


def test_deepseek_thinking_extra_body_effort_mapping() -> None:
    from seed.core.model_providers import apply_chat_thinking_extra_body, normalize_reasoning_effort

    params: dict = {}
    extra: dict = {}
    apply_chat_thinking_extra_body(
        chat_protocol="deepseek",
        base_url="https://api.deepseek.com/v1",
        params=params,
        extra_body=extra,
        resolved_thinking=True,
    )
    assert extra["thinking"] == {"type": "enabled"}
    assert params["reasoning_effort"] == "high"

    params_override: dict = {}
    extra_override: dict = {}
    apply_chat_thinking_extra_body(
        chat_protocol="deepseek",
        base_url="",
        params=params_override,
        extra_body=extra_override,
        resolved_thinking=True,
        reasoning_effort="max",
    )
    assert params_override["reasoning_effort"] == "max"
    assert normalize_reasoning_effort("xhigh") == "max"

    params2: dict = {}
    extra2: dict = {}
    import os

    os.environ["SEED_LLM_REASONING_EFFORT"] = "xhigh"
    try:
        apply_chat_thinking_extra_body(
            chat_protocol="deepseek",
            base_url="",
            params=params2,
            extra_body=extra2,
            resolved_thinking=True,
        )
        assert params2["reasoning_effort"] == "max"
    finally:
        os.environ.pop("SEED_LLM_REASONING_EFFORT", None)


def test_normalize_deepseek_chat_model() -> None:
    from seed.core.model_providers import normalize_deepseek_chat_model

    assert normalize_deepseek_chat_model("deepseek-chat") == "deepseek-v4-flash"
    assert normalize_deepseek_chat_model("deepseek-reasoner") == "deepseek-v4-flash"
    assert normalize_deepseek_chat_model("deepseek-v4-pro") == "deepseek-v4-pro"


def test_materialize_preset_from_form_deepseek_chat() -> None:
    from seed.core.model_providers import materialize_preset_from_form

    out = materialize_preset_from_form(
        {
            "name": "DS",
            "provider": "deepseek",
            "use_type": "chat",
            "model": "deepseek-v4-pro",
            "api_key": "k",
        }
    )
    assert out["model"] == "deepseek-v4-pro"
    assert out["name"] == "deepseek/deepseek-v4-pro"
    assert out["supports_image_gen"] is False

    legacy = materialize_preset_from_form(
        {
            "provider": "deepseek",
            "use_type": "chat",
            "model": "deepseek-chat",
            "api_key": "k",
        }
    )
    assert legacy["model"] == "deepseek-v4-flash"
