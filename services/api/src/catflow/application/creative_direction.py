"""共享的叙事、身份、节拍、结尾与局部编辑方向字符串常量。

本文件是 CatFlow 提示词治理的核心入口 —— 所有 prompt 段落(导演、整片、逐镜、局部编辑)
都从这 5 个常量中按需引用同一份表述,避免各文件重复措辞导致漂移。

各常量的调用方:
- CAT_PERFORMANCE_DIRECTION:整片 / 逐镜 / 局部编辑的"角色"段落都要写一句
- NARRATIVE_DIRECTION:NARRATIVE_DIRECTION 末尾拼接了 ENDING_DIRECTION,所以"叙事"段落一并获得结尾规则
- DIRECTOR_BEAT_DIRECTION:分镜(shot plan)阶段调用,规范 actionBeats 写法
- ENDING_DIRECTION:NARRATIVE_DIRECTION 内部已包含,无需独立调用
- EDIT_PERFORMANCE_DIRECTION:局部编辑(video_repair)阶段独立使用

设计原则:
- 身份 vs 表演分离:猫的眼型、脸型、毛色是身份(固定);眼睑开合、目光是表演(可变化)
- 不以机械连续动作填时长:禁止无变化对视 / 摆尾 / 呼吸循环
- 含义清晰:每条规则都对应一个可观察的视觉行为
"""

from __future__ import annotations

# 猫咪表演方向 —— 描述 V4 白猫的身份(睁眼时的固定特征)与表演(可变化特征)的边界
# 关键约束:
#   - 身份 = 睁眼时的基础眼型 / 眼距 / 虹膜颜色 / 脸型 / 毛色 / 体型
#   - 表演 = 眼睑开合 / 目光方向 —— 允许闭眼后再睁 / 半眯后恢复 / 目光跟随头部
#   - 禁止机械眨眼 / 快速转眼球 / 人类眉毛 / 瞳孔强烈缩放(都属于"过度表演")
CAT_PERFORMANCE_DIRECTION = (
    "固定身份指睁眼时的基础眼型、眼距、虹膜颜色、脸型、毛色和体型，"
    "不固定眼睑开合或目光方向。允许眼睑自然闭合后重新张开、迎风半眯后恢复、"
    "目光先转向明确目标再由头部跟随；眼球不追逐高速旋转的每片叶片。"
    "表演须与触发事件和可读的脸部角度对应，不以机械连续眨眼、快速转眼球、"
    "人类眉毛表演或强烈瞳孔缩放制造生动。"
)

# 结尾方向 —— 禁止无变化对视、摆尾、呼吸循环来"填满"剩余时长
# 强调完成最后动作后允许 0.5-1.5 秒自然停留(不追加新任务)
ENDING_DIRECTION = (
    "允许短暂、有后续回应的目光交流，不让无变化对视、摆尾或呼吸循环填时长。"
    "完成最后动作和情绪回报后允许约0.5至1.5秒自然停留，不追加新任务。"
)

# 叙事方向 —— 拼装 NARRATIVE_DIRECTION = 自身 + ENDING_DIRECTION
# 整片(15 秒)建议 3-5 次有意义的变化,但这是创作建议不是镜头数量配额
# 强调"主要生活事件 + 触发-行动-反应-回报"的事件事件型结构
# 明确要求:在已授权改编范围内,安排猫咪至少一次影响孩子下一步行为的反应
NARRATIVE_DIRECTION = (
    "保持一个主要生活事件，用触发、行动、反应和回报组织时间。"
    "有意义的变化可以是动作结果、注意目标或情绪回应，不只统计身体有没有运动。"
    "在已授权的改编范围内，安排猫咪至少一次能影响孩子下一步行为的反应；"
    "若原文或用户要求限制增写，保留事实并明确说明限制，不擅增事件。"
    + ENDING_DIRECTION
    + "15秒通常安排3至5次有意义的变化，这是创作建议而非镜头数量配额。"
)

# 导演节拍方向 —— 分镜阶段 shot 必填 actionBeats 字段的格式规范
# 字段:startFrame / endFrame(本镜头内 24fps 左闭右开区间)
#        / purpose / childAction / catAction / visibleChange
# 可选:catPerformance(visibility / gazeFrom / gazeTo / eyelidAction)
# eyelidAction 取值:none / blink / slow_blink / squint_release
#   - blink / slow_blink 必须"闭合-重新睁开"过程清楚且脸部可读
DIRECTOR_BEAT_DIRECTION = (
    "【时间节拍与表演】每镜提供actionBeats，通常1至2项；startFrame/endFrame为"
    "本镜头内24fps整数帧的左闭右开区间，按顺序、不重叠、不超出镜头。"
    "purpose为trigger/action/reaction/payoff；childAction/catAction写本时段的角色动作，"
    "不在画面的角色可为空，visibleChange写本时段的新变化。"
    "必要时catPerformance写visibility（visible/partial/hidden）、gazeFrom、gazeTo及"
    "eyelidAction（none/blink/slow_blink/squint_release）。"
    "blink或slow_blink必须设计清楚的闭合—重新睁开过程，并让脸部可读；"
    "目标转移描述对象的位置，不写抽象的‘眼神生动’。"
    "节拍是时间和动作顺序的唯一来源。走位保留初始支撑、空间和结束位置；"
    "movementPath、microMotions及动作摘要应与节拍一致，不另排一套动作。"
    "重复陈设和光线保持简洁；支撑、遮挡、接触面、同一道具数量等必要空间事实不能省略。"
)

# 局部编辑方向 —— video_repair 阶段使用,只在用户明确要求修改的选区安排变化
# 不要求修改的内容 / 选区外内容保持原样
EDIT_PERFORMANCE_DIRECTION = (
    "仅当用户明确要求修改表演时，在指定选区安排对应变化。固定睁眼时的基础眼型不等于"
    "固定眼睑开合和目光；按要求允许闭合后重新张开、自然转移注意目标，"
    "不追加机械眨眼、夸张眼球动作或其他剧情。未要求修改的内容及选区外内容保持原样。"
)