"""图片生成的 provider prompt 编译 —— 把正文与负面约束折叠成 Ark 图片 SDK 的单一 prompt 字段。

当前 Ark 图片 SDK 的请求只接受一个 prompt 文本字段(参考图走独立的 image
数组,不携带职责文本),负面约束没有独立参数,必须折叠进正文:
输出格式固定为「【生成目标】+【必须避免】」两段,两字段均必填,
任一为空即拒绝提交(抛 ValueError),防止缺失负面约束的付费调用。

调用方:
- worker/ark_gateway.py::generate_image —— frozen input 没有预编译的
  compiledProviderPrompt 时的兜底编译;
- 需要声明参考图职责的完整编译路径走 media_prompt.compile_provider_media_prompt
  (额外含【参考职责】段)。
"""

from __future__ import annotations


def compile_provider_image_prompt(*, prompt: str, negative_prompt: str) -> str:
    """构造当前 Ark 图片 SDK 接受的单一 prompt 字段。

    输出固定为"【生成目标】\\n正文\\n\\n【必须避免】\\n负面约束":
    SDK 没有独立的 negative_prompt 参数,负面约束只能作为正文的
    【必须避免】小节一并下发。

    两个字段都必填:任一为空(strip 后)即抛 ValueError 拒绝本次提交 ——
    负面约束是图片质量治理的一部分,缺失视为输入错误,不允许静默降级。

    Args:
        prompt: 生成目标正文(已通过上游校验的完整描述)。
        negative_prompt: 必须避免的内容(禁用项列举)。

    Returns:
        折叠后的单一 prompt 文本,直接填入 SDK 请求的 prompt 字段。

    Raises:
        ValueError: prompt 或 negative_prompt 为空。
    """

    target = prompt.strip()
    exclusions = negative_prompt.strip()
    if not target:
        raise ValueError("image generation prompt is required")
    if not exclusions:
        raise ValueError("image generation negative prompt is required")
    return f"【生成目标】\n{target}\n\n【必须避免】\n{exclusions}"
