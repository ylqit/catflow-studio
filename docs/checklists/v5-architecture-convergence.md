# CAT-VIDEO-GENERATOR V5 架构收敛 checklist

状态说明：`planned` / `implemented` / `unit-checked` / `final-verified`。

本轮保留数据库 HEAD `0018_v5_shot_assistance`，不调用真实 Ark；所有功能完成后只进行一次前端页面点击验收。

## 阶段 1：唯一生成规格

- [implemented] 由同一个 `ShotGenerationSpec` 输出 Provider 模式、真实素材、Prompt、阻塞项和输入哈希。
- [implemented] 制作看板、片段工作区、Prompt 预览与付费提交复用同一规格。
- [implemented] 首帧、多参考图和纯文本三种 Provider 输入模式互斥。

## 阶段 2：批量只读模型

- [implemented] 项目图、制作看板和片段工作区使用一次批量读取结果，不再逐场景、逐片段查询。
- [implemented] 片段工作区不再读取完整 `project_graph` 后重复编译两次 Prompt。

## 阶段 3：任务中心

- [implemented] 增加全局任务聚合接口，以 PostgreSQL 状态为权威。
- [implemented] 只将最新且仍需处理的版本计入角标，历史版本保留在媒体画廊。
- [implemented] 使用前台活跃、空闲和页面隐藏三档轮询策略。

## 阶段 4：剧情双入口

- [implemented] 增加主题扩写步骤，并与完整剧情诊断入口汇合到当前批准剧情。
- [implemented] 分镜是进入视觉制作的唯一必经创作步骤，其余 LLM 阶段按需执行。

## 阶段 5：独立片段生成路由

- [implemented] `StudioView` 仅编排项目与制作看板，片段路由不读取完整项目图。
- [implemented] 片段生成台支持独立 URL、刷新恢复和浏览器返回。
- [implemented] 首帧模式不再提供不会进入视频请求的普通视频参考绑定操作。

## 阶段 6：可选场景视觉基准

- [implemented] 场景视觉基准显示建议原因、跳过、候选与历史版本，不再作为制作阶段硬门槛。
- [implemented] 明确场景视觉基准不是视频首帧。

## 阶段 7：职责清理与文档

- [implemented] 抽离生成规格、批量读模型和成片编排的真实责任边界。
- [implemented] 删除确认无行为所有权的薄包装函数，更新使用和故障恢复文档。

## 阶段 8：一次性前端点击验收

- [final-verified] 使用 fake LLM 完成主题扩写、完整剧情诊断与分镜接受；使用 fake Seedream 完成场景视觉基准和独立开场图的生成、查看、拒绝/批准与历史选择。
- [final-verified] 同一片段依次点击验证三种互斥输入：批准首帧 `first_frame=1`、场景与项目继承 `reference_media=7`、关闭全部继承 `text_only=0`；切换离开首帧模式会原子移除当前 `approved_anchor` 绑定，但保留尾帧历史资产。
- [final-verified] 使用 fake Seedance 完成视频 V1 的非阻塞提交、播放、批准与选择；采用批准视频尾帧作为下一片段唯一开场，页面恢复为 `first_frame=1`。
- [final-verified] 通过页面完成本地 FFmpeg 成片 Revision 1（10.00 秒）的生成、播放入口、批准与选择；场景视觉基准同时验证了“跳过”和“生成候选”两条路径。
- [final-verified] 全局任务中心仅保留两个其他项目中仍需处理的真实任务；被新版本替代的旧候选未污染角标，刷新、项目切换和独立片段路由未造成任务状态振荡。
- [final-verified] 实测中位响应：制作看板 582.9 ms、片段生成台 527.7 ms、Prompt 本地预览 478.3 ms、任务中心 929.3 ms；达到本轮首屏、生成台与 Prompt 目标。
- [final-verified] 重启到独立 fake API 验收日志后，浏览器控制台为 0 error / 0 warning，主要接口无新增 4xx/5xx；健康接口报告数据库 `0018_v5_shot_assistance`、FFmpeg、FFprobe、视频生成和本地合成均 ready。
- [final-verified] `providerMode=fake`、`provider=local-fake-provider`、`realArkCalls=0`。验收截图：`.playwright-cli/page-2026-08-18T13-23-26-803Z.png`（纯文本 0 图）、`.playwright-cli/page-2026-08-18T13-25-40-208Z.png`（上一尾帧唯一首帧 1 图）与 `.playwright-cli/page-2026-08-18T13-37-52-661Z.png`（重启后的 clean session）。
