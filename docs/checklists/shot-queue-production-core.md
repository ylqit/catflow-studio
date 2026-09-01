# 任意场景镜头队列与片段生产 Checklist

本清单只按已经落盘并可从代码、迁移、页面或最终命令结果验证的事实勾选。
实现期间不调用真实 Ark；全部功能完成后只进行一次非付费本地数据流烟测。

## 已确认的产品边界

- 项目由任意数量的有序场景组成，不再强制上午/中午/傍晚。
- 章节标签只是可选文字，不参与校验、解锁或收费。
- 每张镜头卡独立生成一个 8～15 秒视频片段，默认 8 秒。
- 锚点由用户选择：纯文本、已有图片或生成新锚点。
- AI 镜头建议和媒体审核只给建议，不自动打回或自动付费重试。
- 已批准片段不可变；重做产生新 attempt 和新资产版本。
- 项目总片与区间重拍是可选能力，不覆盖源片段。
- PostgreSQL 是唯一工作流状态源；Web 视口和编辑草稿不是状态源。

## Batch 0：对账与迁移边界

- [x] 冻结真实 Ark 调用。
- [x] 将当前契约版本提升为 4。
- [x] 创建 `0015_shot_queue_core`，迁移发现旧生产记录时拒绝继续。
- [x] 归档脚本保存旧 Run、Step、Prompt、Task、媒体与 SHA-256。
- [x] 清理时保留批准 Canon 和已交付本地归档。

## Batch 1：V4 项目、场景和镜头契约

- [x] StoryProject 只保存标题和日期等项目元数据。
- [x] Scene 支持任意新增、删除、排序和可选章节标签。
- [x] ShotCard 支持完整导演描述、8～15 秒、锚点模式和素材职责。
- [x] AI 镜头建议只返回场景标题和任意数量镜头建议。
- [x] 删除领域中的总导演、三时段、场景路线和世界状态契约。

## Batch 2：Repository 与收费意图

- [x] 项目、场景、镜头的增删改排使用短事务。
- [x] Step 与实际 Prompt 原子落库。
- [x] 同输入重复点击复用原 Step。
- [x] 已有 Task ID 只恢复查询，`submission_unknown` 只允许对账。
- [x] 镜头版本与总片 Revision 均可选择和回退。

## Batch 3：镜头建议与 Prompt 预设

- [x] 内置“生活短片镜头化”建议 Prompt。
- [x] 锚点、单镜头视频、审核和区间编辑 Prompt 使用同一镜头事实来源。
- [x] Web 可查看当前编译 Prompt 和每次 attempt 的实际 Prompt。
- [x] JSON 解析失败保存原始输出并转人工整理，不自动重试。

## Batch 4：锚点与外部素材

- [x] `text_only` 不发送图片。
- [x] `existing` 只使用用户选定的一张最终锚点。
- [x] `generate` 创建一个 Seedream 锚点 attempt 并等待人工选择。
- [x] 外部图片记录 usage、role 和使用节点，不自动全量发送。

## Batch 5：一镜一片段、审核和版本

- [x] 每镜独立创建 Seedance task 并保存 Task ID。
- [x] 下载、ffprobe、SHA-256 和原生音轨检查完成。
- [x] 首尾帧、约一秒采样帧和显著变化帧仅作为建议证据。
- [x] 人工批准后才切换镜头正式视频；旧资产仍可回退。

## Batch 6：项目总片与区间重拍

- [x] 按场景、镜头顺序创建项目级 EDL Revision。
- [x] 保留原生音轨并在片段边界使用短淡入淡出。
- [x] 完整镜头选区转为镜头新 attempt。
- [x] 单镜头内部选区生成新版本并保留原版本。
- [x] 跨镜头选区在收费前要求拆分或改为重做镜头。
- [x] 页面不承诺像素一致，只说明区间外沿用原素材及可能的编码差异。

## Batch 7：极简 Web 工作台

- [x] 新建项目只显示标题、第一场景原文和可选参考素材。
- [x] 中央为有序场景与镜头卡，不显示固定三时段空节点。
- [x] 右侧展示 Prompt、素材、Provider、审核和版本。
- [x] 底部为选中片段或总片的单轨时间轴。
- [x] 场景和镜头可以新增、编辑、删除、排序。

## Batch 8：旧代码清理

- [x] 删除总导演、自动三集、SceneRoute、Slot 和固定三时段 Graph。
- [x] 删除故事板、逐镜兼容包装和失效 API/Web 入口。
- [x] 删除无实际边界行为的薄包装与死代码。
- [x] 合并必要证据后删除全部失效 Checklist。

## Batch 9：文档和部署

- [x] README、架构 ADR、完整工作流和 Windows 手册改为 V4。
- [x] Docker 单服务部署继续可用。
- [x] 文档明确哪些按钮会产生真实 Ark 费用。

## Batch 10：最终非付费验收

- [x] `uv run ruff check .`
- [x] `npm --prefix web run build`
- [x] `git diff --check`
- [x] `uv run cvg doctor`
- [x] `uv run python scripts/local_dataflow_smoke.py`
- [x] 烟测证明无三时段 Step、三种锚点、独立镜头重做、总片和区间版本流通。

## 最终验收证据（2026-08-12）

- 数据库已升级到 `0015_shot_queue_core`，`cvg doctor` 返回 `ready=true`。
- 归档清单：`var/diagnostics/v3-archive-before-0015-20260812T001828Z.json`。
- 清单 SHA-256：`8fcefa73eb1ad7d842148501ebdfb76f0f7354672379c20be69e6cebc41d8813`。
- 保留 11 个批准 Canon，清理 52 个未交付媒体文件。
- 离线烟测形成 1 个场景、4 张镜头卡、7 个视频版本和 2 个总片 Revision；
  `submission_unknown` 保持冻结，真实 Ark 调用数为 0。

真实 Ark、画面质量、角色一致性和最终交付由用户随后在 Web 端验证。
