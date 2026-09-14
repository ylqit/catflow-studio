"""Shared narrative and performance policy; static identity is not a frozen expression."""

CAT_PERFORMANCE_DIRECTION = (
    "固定身份指睁眼时的基础眼型、眼距、虹膜颜色、脸型、毛色和体型，"
    "不固定眼睑开合或目光方向。允许眼睑自然闭合后重新张开、迎风半眯后恢复、"
    "目光先转向明确目标再由头部跟随；眼球不追逐高速旋转的每片叶片。"
    "表演须与触发事件和可读的脸部角度对应，不以机械连续眨眼、快速转眼球、"
    "人类眉毛表演或强烈瞳孔缩放制造生动。"
)

ENDING_DIRECTION = (
    "允许短暂、有后续回应的目光交流，不让无变化对视、摆尾或呼吸循环填时长。"
    "完成最后动作和情绪回报后允许约0.5至1.5秒自然停留，不追加新任务。"
)

NARRATIVE_DIRECTION = (
    "保持一个主要生活事件，用触发、行动、反应和回报组织时间。"
    "有意义的变化可以是动作结果、注意目标或情绪回应，不只统计身体有没有运动。"
    "在已授权的改编范围内，安排猫咪至少一次能影响孩子下一步行为的反应；"
    "若原文或用户要求限制增写，保留事实并明确说明限制，不擅增事件。"
    + ENDING_DIRECTION
    + "15秒通常安排3至5次有意义的变化，这是创作建议而非镜头数量配额。"
)

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

EDIT_PERFORMANCE_DIRECTION = (
    "仅当用户明确要求修改表演时，在指定选区安排对应变化。固定睁眼时的基础眼型不等于"
    "固定眼睑开合和目光；按要求允许闭合后重新张开、自然转移注意目标，"
    "不追加机械眨眼、夸张眼球动作或其他剧情。未要求修改的内容及选区外内容保持原样。"
)
