# CAT-VIDEO-GENERATOR V5 专业视觉资产分层 Checklist

状态：`planned` 尚未实现；`implemented` 已落盘并完成代码自检；`final-verified` 已完成最终前端点击验收。

本轮复用 `workflow_steps`、资产 `scope/metadata`、场景视觉草稿和现有审核接口；数据库 HEAD 保持 `0018_v5_shot_assistance`，不调用真实 Ark，不批量新增测试文件。

## 阶段 1：视觉资产规划

- [implemented] 分镜确认后可以运行 AI 视觉资产规划，输出服装、环境、关键道具和构图建议。
- [implemented] 规划只提出生成、上传、选择已有或跳过方案；每张图片仍需独立付费确认。
- [implemented] 原始输出与人工接受稿继续保存在 `workflow_steps`，不增加业务表。

## 阶段 2：项目与场景参考图

- [implemented] 场景与项目均可按职责生成参考图候选，并保存 V1/V2/V3、Prompt、参考输入和审核历史。
- [implemented] 服装、环境和道具资产只能承担声明职责，不能标记为人物或猫咪身份。
- [implemented] 全局 Canon、项目复用资产、场景资产和片段资产保持严格归属。

## 阶段 3：首帧与视频 Prompt

- [implemented] 片段审稿提供可人工编辑和接受的“开场静态画面稿”。
- [implemented] 首帧生成只读取当前草稿版本对应的已接受静态稿，不注入完整视频动作链。
- [implemented] 锚点输入包含片段专用、场景服装/环境/道具、项目身份和画风；视频已有批准锚点时不重复提交已嵌入的场景参考。

## 阶段 4：视觉资产准备工作台

- [implemented] 场景看板增加全局身份、当场造型、环境参考、关键道具和片段首帧五层可视工作区。
- [implemented] 规划版本、人工选择、候选版本、大图、审核和历史版本均可在 Web 查看。
- [implemented] 长任务提交后立即进入全局任务中心，页面保持可操作。

## 阶段 5：最终点击验收

- [final-verified] 使用 fake LLM/Seedream 点击“放风筝”完整链路，真实 Ark 新调用数为 0。
- [final-verified] 验证环境、服装和风筝资产归属、版本、引用顺序、首帧静态稿与视频 Prompt 分离。
- [final-verified] 检查浏览器控制台、接口错误、任务中心和主要布局。
