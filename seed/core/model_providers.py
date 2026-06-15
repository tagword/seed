"""
Backward-compatible re-export shim.

``seed.core.model_providers`` has been extracted to the standalone
``seed-model-providers`` package.  This shim re-exports everything
from ``seed_model_providers`` so existing imports continue to work::

    from seed.core.model_providers import resolve_chat_protocol   # still works
    from seed_model_providers import resolve_chat_protocol         # canonical way

New code should import directly from ``seed_model_providers``.
"""

# flake8: noqa: F401, F403
from seed_model_providers import (  # type: ignore[import-untyped, unused-ignore]
    PROVIDER_CATALOG,
    USE_TYPE_LABELS,
    apply_chat_thinking_extra_body,
    call_agnes_video_generation,
    call_image_generations,
    call_minimax_music_generation,
    call_minimax_video_generation,
    call_music_generations,
    call_video_generations,
    default_max_request_body_bytes,
    enrich_preset_defaults,
    enrich_presets_for_ui,
    get_provider_spec,
    infer_preset_use_type,
    infer_provider_from_url,
    infer_use_type_for_provider_model,
    list_models_for_provider,
    list_provider_catalog,
    materialize_preset_from_form,
    model_label_for_provider,
    normalize_deepseek_chat_model,
    normalize_image_size,
    normalize_provider_id,
    normalize_reasoning_effort,
    normalize_video_num_frames,
    normalize_volcengine_image_model,
    preset_auto_id,
    preset_auto_name,
    preset_display_fields,
    preset_display_name,
    provider_requires_api_key,
    resolve_chat_protocol,
    resolve_image_protocol,
    resolve_music_protocol,
    resolve_provider_for_preset,
    resolve_video_protocol,
    should_send_reasoning_content,
    uses_deepseek_chat_protocol,
)

# Private helpers — re-exported from the module directly for tests
# (not exposed via seed_model_providers public API)
from seed_model_providers.model_providers import (  # type: ignore[import-untyped, unused-ignore] # noqa: E402, F401
    _agnes_videos_url,
    _minimax_music_url,
    _minimax_video_url,
    _auth_headers,
    _images_url,
    _decode_image_item,
    _default_model_id,
    _minimax_image_url,
    size_to_minimax_aspect_ratio,
    size_to_volcengine_size,
)
