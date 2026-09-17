"""确定性 JSON 文本修复 —— 只做无歧义的语法修复,永不猜测内容。

背景:模型以 json_object 模式(无约束解码)返回结构化正文时,偶尔在字符串值内
输出未转义的英文双引号(如台词引用),导致 json.loads 失败、任务落
invalid_structured_output,创作者只能人工修 JSON。本模块提供一个单遍状态机,
把"不可能是字符串结束符"的引号转义后重试解析:

判定规则(无歧义):串内未转义 `"` 只有当其后首个非空白字符属于 `, } ] :`
或到达文末时才可能是合法的字符串结束;否则该引号必是字符串内容,转义为 `\\"`。

边界与仓库哲学:
- 截断正文(字符串未闭合、括号未配平)不可修,原样抛出原始解析错误,
  走既有 incomplete / 人工补充路径,不补括号、不臆造内容;
- 修复计数以 textRepairs 审计透传落库,界面以 warning 提示创作者核对;
- 修复后的 payload 仍要过冻结契约校验,错误内容不会被静默采纳。
"""

from __future__ import annotations

import json
from typing import Any

# 字符串合法结束后,首个非空白字符只可能是这四种结构符(或文末)
_STRUCTURAL_FOLLOWERS = frozenset(",}]:")
_WHITESPACE = frozenset(" \t\r\n")


def escape_unstructural_quotes(text: str) -> tuple[str, int]:
    """转义字符串值内不可能是结束符的英文双引号;返回(修复文本, 修复计数)。

    单遍状态机:尊重反斜杠转义序列;串内 `"` 后若无结构符跟随则判定为内容。
    对合法 JSON 是恒等变换(计数为 0)。
    """
    out: list[str] = []
    repairs = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if not in_string:
            out.append(char)
            if char == '"':
                in_string = True
            continue
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if char == '"':
            following = index + 1
            while following < len(text) and text[following] in _WHITESPACE:
                following += 1
            if following >= len(text) or text[following] in _STRUCTURAL_FOLLOWERS:
                in_string = False
                out.append(char)
            else:
                out.append('\\"')
                repairs += 1
            continue
        out.append(char)
    return "".join(out), repairs


def parse_json_object(text: str) -> tuple[dict[str, Any], list[str]]:
    """解析模型正文为 JSON 对象;严格解析失败时尝试一次确定性引号修复。

    返回 (payload, textRepairs):严格解析成功时 textRepairs 为空列表;
    修复成功时 textRepairs 为 ["escaped_unstructured_quotes:N"]。
    不可修复(截断、多余数据等)时重抛**原始**解析异常,保持既有
    invalid_structured_output 的报错语义不变。
    """
    repairs: list[str] = []
    try:
        payload = json.loads(text)
    except ValueError as original:
        repaired, count = escape_unstructural_quotes(text)
        if count == 0:
            raise
        try:
            payload = json.loads(repaired)
        except ValueError:
            raise original from None
        repairs.append(f"escaped_unstructured_quotes:{count}")
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload, repairs
