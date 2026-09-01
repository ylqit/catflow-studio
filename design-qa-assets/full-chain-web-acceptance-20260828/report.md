# 四区导演画布与纯 Web 全链路验收报告

## 1. 验收范围与结果

- 日期：2026-08-28（Asia/Hong_Kong）
- 浏览器：Codex 内置浏览器
- 项目：`一人一猫-Web全链路试验-20260828-r1`
- Project ID：`26f8693e-5445-40fe-99e5-2169eadc280b`
- Recipe ID：`5596c472-0010-4f45-9bb2-0dd50521aa20`
- 最终 URL：`/canvas/26f8693e-5445-40fe-99e5-2169eadc280b?zone=production&node=712bc5a8-3a07-57f5-aa44-d4045dcdf3b9`
- 目标：8 秒、quick、9:16、`reference_media`、Scene Look off、首帧锚点关闭
- 结果：通过。新项目从创建、剧情、角色设计、分镜、环境参考、Prompt、制作包、视频、时间线、本地合成、人工批准到浏览器下载均由真实 Web UI 完成。
- 最终四区：剧情与脚本、人物／猫咪与视觉圣经、分镜与镜头设计、视频生成与成片均为“已完成”。
- 最终视频资产：`48b9f36b-6ed6-4699-a3f7-289928561314`
- 最终音画资产：`06b8a26d-d978-4ab9-8666-63458f39306e`，版本 2，人工批准。
- Provider 视频任务 ID：`cgt-20260828200522-mbccc`
- 浏览器下载：`C:\Users\wwwab\Downloads\content (13).mp4`，975,542 字节；页面探测时长 8.1 秒，可播放，9:16。

所有业务状态变更均来自 Web 页面操作。未使用 CLI、SQL、repository、service、Provider gateway、DevTools `fetch()`、DOM/Vue 状态修改或直接写 API 绕过业务流程。终端仅用于修改代码、测试、构建、服务重启和只读日志检查。

## 2. Provider 与费用边界

| 类型 | 实际调用 | 上限 | 结果 |
|---|---:|---:|---|
| 剧情候选 Director | 1 | 1 | 生成 3 个长文本候选，选择并保存当前剧情 |
| 本集角色设计图片 | 3 | 3 | 儿童、猫咪、同框比例各 1 次并审核通过 |
| 分镜 Director | 1 | 1 | 生成后在锚定工作台整理为 3 个导演分镜、总时长 8 秒 |
| 视觉资产规划 Director | 1 | 1 | 生成后通过人工修订只保留环境参考 |
| 环境参考图片 | 1 | 1 | 生成、人工审核并绑定 |
| 首帧／Scene Look | 0 | 0 | 保持关闭，不作为视频硬门禁 |
| 视频 | 1 | 2 | 第一次任务返回可用视频；未创建第二个视频任务 |
| 本地合成／导出 | 0 个 Provider 调用 | 0 | FFmpeg 本地处理首尾 500ms 淡黑并导出 |

费用确认层在每次付费媒体操作前显示 Provider、模型、参考清单、调用次数和冻结输入；取消确认路径未创建任务。未在 `submitted`、`running`、`submission_unknown` 或 `cancellation_unknown` 状态重复提交。

## 3. Web 链路执行记录

| 顺序 | 导演区／节点 | Web 操作 | 实际结果 | 分类 | 截图 |
|---:|---|---|---|---|---|
| 1 | 全局任务中心 | 打开旧项目待提交任务，确认无 Provider task ID 后点击“取消，尚未提交 Provider” | 冷刷新后保持 cancelled；Provider 调用 0 次 | pass | `01-local-task-cancelled-1440x900.png` |
| 2 | 剧情与脚本 | 创建新项目、应用 Canon v3 证据、创建 8 秒 quick Recipe、编辑 Brief、生成候选、选择并编辑当前剧情 | 当前剧情成为正式版本，候选保留为历史分支 | pass | `02-story-approved-1440x900.png` |
| 3 | 人物、猫咪与视觉圣经 | 分别生成儿童、猫咪、同框比例设计并逐项审核 | 3 张本集设计均批准；未复用旧项目角色设计 | pass | 过程记录在任务中心与角色审核状态中 |
| 4 | 分镜与镜头设计 | 生成分镜、在节点锚定工作台整理为 3 个镜头、调整顺序与时长并保存 Revision | 3 个导演分镜分别为 2s、3s、3s；生成编排合并为 1 个 8 秒真实视频片段 | pass | `03-storyboard-structured-draft-1440x900.png`、`04-storyboard-workbench-1440x900.png` |
| 5 | 环境与参考 | 生成视觉资产规划，人工修订非必需槽位为 skip，生成并审核环境参考 | Scene Look 保持 off；环境图作为 `reference_media` 输入 | pass | `05-environment-candidate-1440x900.png` |
| 6 | Prompt／制作包 | 编译生产 Prompt，核对人物、猫咪、本集设计、环境参考和哈希，批准制作包 | 参考清单冻结；无首帧与 Scene Look 依赖 | pass | `06-prompt-editor-1440x900.png`、`07-video-frozen-input-1440x900.png` |
| 7 | 视频生成 | 在费用确认层确认一次 8 秒 `reference_media` 视频生成 | 创建一个 Provider 任务 | pass | `08-video-cost-confirm-1440x900.png` |
| 8 | 任务取消语义 | Provider queued 时从 Web 请求取消；服务端重新对账后发现 Provider 已 running | 拒绝伪取消，继续跟踪原任务；未创建第二任务 | pass | `09-provider-queued-cancellation-1440x900.png` |
| 9 | 视频审核 | 页面恢复真实视频候选，完整播放至 8 秒；填写人工覆盖理由并批准 | 身份连续、猫咪四足、纸星星动作和结尾阳光可读；成为当前视频版本 | pass with warning | `10-video-review-1440x900.png`、`12-video-review-lateframe-1440x900.png` |
| 10 | 时间线 | 将唯一批准视频加入时间线，开启开场／结尾各 500ms `fade_black` | 仅本地剪辑，不向视频模型发送黑图 | pass | `13-timeline-boundary-fades-1440x900.png` |
| 11 | 本地合成 | 点击“合成最终成片” | 首次因 Worker 仍加载旧请求模型失败；重启 Worker 后从同一 Web 按钮重试成功 | recovered error | `14-final-sequence-review-midframe-1440x900.png` |
| 12 | 最终审核与导出 | 完整播放最终成片，点击“批准最终成片”，再点击 Web 下载链接 | 最终音画批准；下载文件非零且可播放 | pass | `15-timeline-export-approved-1440x900.png` |
| 13 | 冷刷新恢复 | 冷刷新并通过深链接恢复最终节点 | 四区全完成；时间线、最终资产、批准和导出入口均恢复 | pass | `22-final-cold-refresh-production.png` |

## 4. 任务取消语义验收

### 4.1 尚未提交 Provider

- 页面文案：“取消，尚未提交 Provider”。
- 前置事实：无 Provider task ID、无提交时间、页面取消策略为 `local_before_provider`。
- 结果：Web 取消成功，冷刷新保持 cancelled；未出现 Provider create 请求，Provider 调用 0 次。

### 4.2 Provider 已提交

- 新视频任务最初显示 Provider queued，用户通过 Web 请求取消。
- 服务端在取消前重新查询真实 Provider 状态，发现已进入 running，因此没有把本地隐藏或停止跟踪冒充远端取消。
- 页面切换为“Provider 已运行，无法取消”，继续跟踪同一任务；没有创建第二个同输入任务。
- 后续发现 Provider 状态曾被较旧 queued 观察值回写，导致页面错误恢复取消按钮。已修复为单调状态合并：`running` 不会回退为 `queued`，终态不会被非终态覆盖；相关后端和前端测试通过。

### 4.3 未知态

- 本次没有产生 `submission_unknown` 或 `cancellation_unknown`。
- 代码与测试继续保证两种未知态都不能重试或本地宣称取消成功。

## 5. 途中发现并修复的问题

| 问题 | 页面／网络证据 | 修复结果 |
|---|---|---|
| 固定底部 Composer、固定 Inspector 与 Drawer 脱离节点 | 旧页面截图中编辑器覆盖浏览器底部，来源节点和连线不可读 | 收敛为节点锚定编辑器；分镜在同一空间工作台内编辑，支持普通／展开态 |
| 页面流程需要从 30+ 节点猜测 | 四区只有粗粒度状态，下一步不明确 | 服务端投影 `steps` 与 `nextAction`，页面显示紧凑导演路径和唯一下一步 |
| 剧情与分镜过度 Schema／门禁 | 旧流程需要多轮评分与拆分字段 | 剧情使用长文本候选；分镜保留标题、direction、时长和可选高级导演参数；质量只 warning |
| 冷启动可长期停留骨架屏 | 页面无错误、无重试 | 请求加入 15 秒超时、首次错误态、后台 stale-success 与单活动请求约束 |
| 新前端调用旧 API 路由返回 405 | `storyboard-production-confirmations` 在源码存在但运行进程未更新 | health 暴露 `apiFeatures`，前端在业务 POST 前阻断版本错配；开发 API 支持 reload |
| Storyboard 生成返回嵌套结构无法解析 | 页面显示原始文本，无法形成镜头 | 宽松保存原始输出并支持在锚定工作台手工整理；不丢失模型正文 |
| Storyboard 保存 409 与 Scene 唯一约束 500 | Web 保存失败 | 修正 Revision／场景物化事务；用户从原页面重新保存成功 |
| Prompt 编译阻断却被前端误报成功 | 页面 toast 与节点真实状态不一致 | 前端检查编译响应 `ready/blockers`，不再伪报成功 |
| Scene Look 文案与门禁矛盾 | 页面说可选但 readiness 仍阻断 | 默认 off；环境参考可直接进入多参考视频输入 |
| 已接受视觉规划看似可编辑但无法保存 | 刷新后 skip 丢失 | 增加人工修订版本，零 Provider 调用，保存后成为当前版本 |
| 生成任务 running 回退 queued | 取消后轮询恢复错误按钮 | Provider 状态单调合并，取消策略基于持久化 running 事实 |
| ReviewNode 无真实视频候选 UI | 只能看到状态，无法播放审核 | 增加节点内视频播放器、诊断、覆盖理由、退回、局部编辑和批准 |
| Timeline 步骤误指向 VideoSegment | 点击时间线仍回到生成节点 | 优先绑定 TimelineNode，并嵌入真实时间线与最终成片播放器 |
| Provider 结果已落库但 Worker 报 `workflow lease heartbeat failed` | 视频任务最终 failed，但页面已有真实候选 | 保留可用 Provider 结果并允许人工审核；失败仍保留在任务中心审计，不再重新生成 |
| 本地合成首次失败 | Worker 原始错误：`RecipeSequenceRunRequest` 不识别 `introTransition/outroTransition` | 重启加载新模型后，只通过 Web 时间线重试成功；无 Provider 调用 |
| 已完成区仍被历史候选／旧任务重新标为待审核或受阻 | 冷刷新后路径步骤全完成，但四区徽标仍显示待处理 | 以导演步骤完成态作为区域最终摘要；旧任务继续保留在节点和任务历史，不迫使用户重做 |
| 宽屏顶栏状态与全局任务按钮可能重叠 | 1920×1080 视觉回归 | 顶栏为固定任务入口预留空间；1280 与 1920 实测边界无重叠 |

历史开发期 Console 记录包括一次角色设计费用参数错误和一次 HMR 期间临时渲染错误，均已记录。最终两次冷刷新后的最新日志只有 Vite `connecting/connected` debug 信息，没有新增 warning/error 或未处理 Promise。

## 6. 自动化门槛

- 后端：`485 passed`，仅 1 个 Starlette/httpx 弃用 warning。
- 前端：`39` 个测试文件、`151 passed`。
- `vue-tsc --noEmit`：通过。
- Vite 正式构建：通过；存在单包大于 500kB 的性能 warning，不影响本轮功能验收。
- `git diff --check`：通过；仅报告 Windows 工作区 LF/CRLF 提示，无空白错误。
- 重点新增回归：导演路径完成态、Provider 状态单调性、取消策略、首尾本地淡黑、边界转场契约、时间线序列请求、最终播放器与人工批准。

## 7. 视觉回归与对照

- `16-final-four-zones-1280x720.png`
- `17-final-four-zones-1440x900.png`
- `18-final-four-zones-1920x1080.png`
- `19-final-storyboard-editor-1440x900.png`
- `20-libtv-storyboard-side-by-side.png`：同一比较画布中的 LibTV 空间节点参考与本项目锚定分镜工作台。
- `21-before-after-node-editor.png`：旧固定底窗与新节点锚定编辑器直接对照。

对照结论：新版编辑器与来源节点保持空间关系，画布和连线继续可见；主输入和镜头列表无需第二入口；分镜在一个可滚动的 800×480 左右节点工作台内完成，未恢复固定底窗或右侧 Drawer。界面借鉴 LibTV 的空间编辑范式，但未复制其品牌、素材或源码。

## 8. 已知非阻塞项

- 已批准 Beat 卡片仍会显示关联历史视频工作流的失败徽标；导演路径与四区状态已正确完成，任务中心仍保留失败审计。后续可把该徽标改成“历史失败，已有批准产物”，进一步降低误解。
- Vite 构建主 JS 约 1.79MB，建议后续按 Canvas、媒体审核、视频编辑和管理页做动态拆包。
- 最终视频专项语义诊断返回结构不可识别，因此本次按页面要求填写人工覆盖理由；这不阻断已完整人工播放的可用结果。

报告未保存 API Key、Authorization、Cookie、浏览器存储、Provider 签名 URL或其他凭据。
