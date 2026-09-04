from __future__ import annotations


def compile_provider_image_prompt(*, prompt: str, negative_prompt: str) -> str:
    """Build the single prompt field accepted by the current Ark image SDK."""

    target = prompt.strip()
    exclusions = negative_prompt.strip()
    if not target:
        raise ValueError("image generation prompt is required")
    if not exclusions:
        raise ValueError("image generation negative prompt is required")
    return f"【生成目标】\n{target}\n\n【必须避免】\n{exclusions}"
