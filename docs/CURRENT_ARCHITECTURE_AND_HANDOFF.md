# CatFlow Studio 当前架构与开发交接

> 状态日期：2026-09-04  
> 仓库：`D:\soft\code\OpenGit\catflow-studio`  
> 分支：`codex/target-first-web`  
> 基线提交：`c04bdb6 fix: 修复分镜 Schema 分层校验与非阻断结果恢复`

本文是后续开发会话的首要交接入口，描述当前代码已经实现并经过核验的系统，而不是未来愿景。发生冲突时，事实来源的优先级为：

1. 当前代码、Alembic migration 与生成的 OpenAPI；
2. 本文；
3. 根目录 `README.md`；
4. `docs/http-api.md` 和旧 workflow 文档。

最后一类文档仍包含旧 V5/Scene/Shot API 资料，只能作为历史设计参考，不能据此修改当前接口。

## 1. 产品定位与不可变边界

CatFlow Studio 是本机单用户的一人一猫原创生活短片工作室，目标输出为 8–15 秒、9:16 的温暖日常微事件视频。默认角色是同一位 6–7 岁短发儿童和同一只灰白虎斑猫，使用固定的柔和二维数字插画语言。

创作主线固定为：

```text
故事灵感
  → 角色与画风
  → 分镜画布
  → 生成与选择
  → 剪辑与导出
```

产品层的重要决定：

- 默认页面只呈现创作者需要理解的内容；Job、Provider task ID、SHA256、input hash、capability revision 等进入“生成记录/技术详情”。
- 技术审计能力不能因界面精简而删除：完整 Prompt、Negative Prompt、参考资产、usage、费用、版本来源和 Provider 状态仍可追溯。
- 故事、分镜、媒体、生成输入和剪辑版本均采用不可变版本或追加记录；人工采用前不得改变当前版本。
- 正式运行只支持 Ark，不存在 Fake Provider 或运行时测试模式回退。
- 不引入 ToonFlow Agent、Socket.IO、Electron、SQLite、第二视频 Provider 或第二业务状态源。
- `style_source` 是历史设计源，不得进入图片或视频 Provider 请求。
- 真实 Ark 调用可能产生费用。没有用户对该次操作的明确授权，不应由开发或验收流程触发。

## 2. 系统上下文

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Vue 3 + TypeScript + Vite（apps/web）                               │
│ 项目库 / 五步工作区 / 图片查看器 / 视频预览 / 生成记录              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 同源 REST、SSE、媒体读取
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ FastAPI 模块化单体（services/api）                                  │
│ DTO / 业务服务 / 输入冻结 / 幂等校验 / 版本采用 / 安全准入          │
└───────────────┬───────────────────────────────┬─────────────────────┘
                │                               │
                ▼                               ▼
┌───────────────────────────┐       ┌─────────────────────────────────┐
│ PostgreSQL `catflow`      │       │ 本地受管文件                    │
│ 唯一业务状态源            │       │ var/media、var/work、var/logs   │
└───────────────┬───────────┘       └─────────────────────────────────┘
                │ durable Job
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Worker Supervisor → Durable Worker（services/worker）               │
│ Ark typed gateway / 结果落盘 / TOS 发布 / FFmpeg / 项目封面         │
└──────────────────────┬──────────────────────┬───────────────────────┘
                       │                      │
                       ▼                      ▼
                火山方舟 Ark          TOS S3 兼容临时对象发布
```

FastAPI 固定绑定 loopback，默认地址为 `http://127.0.0.1:8877`。它同时提供 Vue SPA、REST、SSE 和媒体内容，因此正式环境不需要 Nginx 或跨域前端服务。

## 3. 技术栈与仓库地图

### 3.1 技术栈

- 后端：Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、psycopg。
- 前端：Vue 3、TypeScript、Vite、Pinia、Vue Router、Vitest、TDesign、WebAV。
- 数据库：PostgreSQL，Schema 为 `catflow`。
- 模型：火山方舟 Ark；规划/诊断、Seedream 图片、Seedance 视频均通过 typed gateway 调用。
- 媒体：Pillow、FFmpeg、ffprobe；正式媒体保存在项目内 `var/media`。
- 临时发布：TOS S3 兼容私有对象和短期预签名 URL，仅用于把本地视频上下文交给 Ark。
- 契约：FastAPI OpenAPI → `packages/contracts/openapi.json` → `packages/contracts/src/schema.d.ts`。

### 3.2 目录职责

```text
apps/web/                         唯一正式 Web 前端
  src/views/                      项目库、工作区、运行设置
  src/components/workspace/       五步创作组件和图片/视频编辑组件
  src/api/                        类型化 API 客户端
services/api/
  src/catflow/application/        应用服务、命令/DTO、Provider 配置
  src/catflow/domain/             故事、分镜、Job、计费、参考和修复规则
  src/catflow/infrastructure/     PostgreSQL、媒体、TOS、仓储实现
  src/catflow/interfaces/         FastAPI 和 CLI 入口
  src/catflow/maintenance/        可审计的清理维护流程
  alembic/                        当前数据库迁移链
services/worker/                  Durable Worker、Supervisor、Ark 和本地媒体任务
packages/contracts/               生成的 OpenAPI 与 TypeScript 契约
assets/canon/v4/                  正式 Canon 生产资产
scripts/                          配置、启动、停止、备份、恢复、契约生成
scripts/legacy/                   临时保留的单向遗留资产导入器
tests/                            后端、契约、E2E 与一致性测试
var/media/                        正式媒体（Git 忽略）
var/work/                         Worker 状态和中间文件（Git 忽略）
var/logs/                         API/Worker 日志（Git 忽略）
var/backups/                      备份和清理隔离区（Git 忽略）
风格定稿/                         历史设计源，只用于人工审计
```

## 4. 数据模型

当前 ORM 业务表包括：

- `canon_profiles`：Canon 版本和固定角色/画风配置。
- `projects`、`project_collections`、`project_tags`：项目库、一级收藏夹和主题标签。
- `life_planner_sessions`、`life_planner_messages`、`life_planner_proposals`：故事规划会话和候选。
- `story_versions`：不可变故事版本。
- `assets`：图片、视频、候选、锚帧、封面和成片等媒体元数据；只保存相对 `storage_key`。
- `project_selections`：项目当前选中的角色、猫咪、比例、环境、画风、视频和最终成片。
- `shot_plan_versions`：不可变分镜版本及 candidate/accepted/rejected/superseded 审核状态。
- `jobs`、`job_events`：持久化异步任务、生命周期事件、冻结输入、Provider ID、usage 和费用。
- `media_publications`：局部编辑上下文的临时 TOS 发布记录。
- `edit_versions`：不可变 EDL/时间线版本。
- `video_repairs`：历史表名；产品和公共 API 使用 `video-edits` 语义。
- `provider_rate_cards`：不可变费率版本。
- `validation_runs`：只读保留的历史发布验收证据，不再授权普通创作。

核心关系：

```text
Project
├─ StoryVersion（一个 active，多条历史）
├─ ProjectSelection（每个 slot 当前一条，历史追加）
├─ ShotPlanVersion（一个 active，可有一个待确认 candidate）
├─ Job → JobEvent / Asset / MediaPublication
├─ EditVersion（一个 active，多条历史）
└─ VideoRepair → Candidate Asset → Approve → 新 EditVersion
```

### 4.1 当前迁移链尾部

- `0017_project_library`：收藏夹、标签、固定、归档和项目库索引。
- `0018_remove_obsolete_job_kinds`：移除 Base64 视频探针 Job kind。
- `0019_project_scoped_environment`：环境选择改为项目级，移除全局 active environment 语义。
- `0020_shot_plan_review_workflow`：分镜 candidate/accepted/rejected/superseded 审核工作流。

当前数据库已核验在 `0020_shot_plan_review_workflow (head)`。

## 5. 关键业务工作流

### 5.1 故事灵感

Life Planner 生成结构化生活微事件，核心字段是触发、孩子动作、猫咪反应、可见变化、温暖结尾和环境意图。采用候选才创建新的 active `StoryVersion`。

新 Prompt 约束避免套话和重复用户输入；标题、摘要和各结构字段应提供不同的创作信息。故事变化会使依赖它的分镜显示为输入已变化，但不会删除历史分镜或已生成视频。

### 5.2 角色、画风与环境

正式生成参考槽位为：

1. 儿童设计；
2. 猫咪设计；
3. 人猫同框比例；
4. 当前项目环境；
5. Canon v4 净化画风板。

环境是项目级选择，不再跨项目共享。环境图片应为空场景，只决定空间、道具、天气、构图和光线，不应出现儿童、成人、猫咪或其他动物。

环境 Seedream 请求固定使用三张参考：

```text
style_board → episode_child → episode_cat
```

儿童和猫咪参考只提供渲染语言，不允许复制主体；旧环境、比例板和 `style_source` 不进入该请求。由于当前图片 SDK 没有独立 negative-prompt 参数，Gateway 会在唯一 Provider 边界把“生成目标”和“必须避免”编译为实际发送 Prompt，同时数据库继续分开保存两部分。

前端 `AssetImageViewer` 支持原图查看、缩放、平移、候选切换、键盘操作，以及环境与四张固定参考的并排对照。

### 5.3 分镜生成、版本和分层校验

Director Planner 将 active Story、当前选择、目标时长、基础分镜和版本信息冻结进 `plan_shots` Job。重新生成成功时只创建 inactive candidate；旧 active 分镜继续使用，直到用户明确采用。

版本状态：

- `accepted`：已采用；其中最多一个 active。
- `candidate`：新生成、待确认；项目最多一个未过期候选。
- `rejected`：用户未采用。
- `superseded`：被较新候选取代。

Provider 结果采用三层校验：

1. **可解析边界**：只硬性保证 completed、合法 JSON、对象/数组/字符串/数字等基础结构和响应大小/深度安全。
2. **候选完整性**：至少一个可解析镜头即可保存；结果分为 `candidate_ready`、`needs_input`、`invalid`。
3. **采用/视频生成门控**：1–4 个镜头、24 fps 总帧闭合、必需动作/画面字段、动作起点—路径—终点、物理变化、主动结尾、儿童安全、输入版本一致性等继续是硬规则。

声音条目、微动作和风险数量属于创作密度建议，不是 Provider 结构门控。4 条 `objectEffects` 会完整保留并产生 warning，不会使已付费结果报废。Provider 未知字段保留在原始 Job Payload 中，但不进入正式 `ShotSpec` 或 Seedance Prompt。

两个无付费恢复入口：

- `POST .../shot-plans/generations/{jobId}/recover`：从历史 Provider Payload 重新归一化并恢复候选。
- `POST .../shot-plans/generations/{jobId}/materialize`：用户补齐 blocking 字段后生成候选。

两者都不调用 Ark、不增加 usage、不覆盖历史 Prompt 和 Provider ID。

### 5.4 整片视频生成

生成页先显示当前输入快照预览；Preview 不创建 Job、不调用 Ark、不计费。Create 会重新编译输入并核对 `expectedInputHash`，防止预览后输入已变化。

Seedance 五张参考顺序由服务端固定：儿童、猫咪、比例、环境、画风。Job 成功返回的候选与其实际冻结 Prompt 永久关联；历史候选不能用当前项目状态重算 Prompt。

候选成功不会自动成为最终成片。用户选择视频后，项目才进入剪辑阶段。

### 5.5 局部视频修改

首版是时间区间语义编辑，不是空间画笔蒙版，也不包含视频续写。新 API 和页面没有“修改类型”，同一区间内动作、道具、环境等相关要求保留原顺序并作为一次 Job 提交。

编辑基准为固定 24 fps，所有区间使用左闭右开 `[startFrame, endFrame)`：

- 用户问题区间最短 96 帧（4 秒），最长 360 帧（15 秒）。
- 左右手柄、数字输入、I/O 和键盘逐帧操作共用同一约束，不能越界、交叉或推动另一个手柄。
- `issueRange`、`generationRange` 和 `candidateCoreRange` 各自独立。
- Provider 可使用扩展上下文，但候选核心必须与问题区间等长，不能变速或拉伸补齐。

局部编辑上下文先经 SHA256/ffprobe 校验，再发布到私有 TOS 并以短期 HTTPS URL 提交 Ark。URL、密钥、本地路径和 `storage_key` 不进入公共 DTO。

候选必须经过人工质量检查后才能采用。采用事务重新校验 active EditVersion、timeline hash、Job、Asset 和 SHA256，然后创建新的不可变 EDL v2/EditVersion。默认硬切；可选择 2/4/6 帧叠化，但最终总帧数不变。候选音轨永不进入正式成片，根视频原音轨保持不变。

### 5.6 项目库

项目库使用服务端游标分页，默认每页 36 条，不会一次渲染数百项目。一个项目最多属于一个一级收藏夹，可拥有最多 8 个主题标签；标签使用“同时包含”语义筛选。

系统视图包括全部、最近更新、创作中、待处理、已完成、已固定和已归档。`lastActivityAt` 由项目、故事、分镜、选择、Job 和 EditVersion 的真实活动动态投影，不把 `projects.updated_at` 当作唯一依据。

归档不删除任何业务数据；存在运行任务的项目拒绝归档。批量操作在一个事务中完成，不支持批量永久删除。

### 5.7 用量与费用

普通创作没有 CatFlow 自定义额度。每个真实 Job 保存 Provider 返回的实际 usage；缺失字段保持缺失，不能补零。若有有效的不可变费率快照，系统计算费用；无费率时显示“待核价”，不能显示 ¥0。

页面区分“按费率表计算”和“Provider 最终账单”。Validation Run 只作为历史发布验收记录保留，不再阻止普通任务。

## 6. Job 生命周期、幂等与恢复

Job 状态机为：

```text
queued → submitting → submitted → polling → storing → succeeded
                         └───────────────→ failed
submitting ─→ submission_unknown
任意允许阶段 ─→ cancel_requested → cancelled/failed
```

核心安全规则：

- 同一幂等键和相同输入返回原 Job；同一键对应不同输入返回 `409 idempotency_input_conflict`。
- 浏览器在 API 成功返回持久化 Job ID 后立即结束该 HTTP 请求的幂等周期；网络结果未知时保留原键，防止重复付费。
- 已有非终态同类 Job 时，按钮保持禁用。
- `provider_submission_started_at` 之前的内部异常可确定性失败；提交开始后、task ID 落盘前的异常必须进入 `submission_unknown`，不得自动重提。
- 已有 `provider_task_id` 时，Worker 重启只能继续轮询和落盘，不得重新发布或提交。
- Job 的冻结输入、input hash、Prompt、参考 SHA256、Provider ID、usage 和费率快照是历史审计事实。

Worker 每 5 秒原子更新心跳。API 只有在 readiness 文件合法、PID 存在且心跳不超过 15 秒时才允许新异步任务。Worker 离线或心跳过期时，所有异步写入口在创建 Job 前返回 `503 worker_unavailable`；读取、Preview、手工保存、选择和采用已有结果仍可用。

`catflow-worker supervise` 在 Worker 异常退出后按退避策略自动重启。单个 Job 的异常被隔离，不能终止整个 Worker。

## 7. API 分组

当前公共接口以 `packages/contracts/openapi.json` 为准，主要分组如下：

- Runtime：health、bootstrap、settings、rate cards、object publisher check。
- Project library：分页读取、收藏夹、标签、项目组织、批量操作。
- Workspace：项目、工作区聚合、Planner、Stories、Selections、Assets。
- Shot plans：列表、保存、生成、生成记录、recover、materialize、activate、reject。
- Image/video：asset preview/create/diagnose、video preview/create/diagnose。
- Segment editing：`video-edits` preview/create/list/detail/approve/reject。
- Delivery：edits、exports、final selection、媒体内容读取。
- Jobs：detail、usage、cancel、resume storage、SSE events。
- Validation：只读 current/detail。

旧 `/video-repairs` 写接口、Validation Run 写接口和 Base64 视频探针不在当前 OpenAPI 中。

## 8. 前端信息架构

正式路由：

```text
/projects                              项目库
/projects/:projectId/planner           故事灵感
/projects/:projectId/assets            角色与画风
/projects/:projectId/storyboard        分镜画布
/projects/:projectId/generation        生成与选择
/projects/:projectId/delivery          剪辑与导出
/settings                              运行设置
```

生成相关页面统一使用三层信息：

1. 创作者内容：要生成什么、是否可生成、费用提示、当前进度和输出。
2. 制作信息：完整 Prompt/Negative Prompt、参考图、模型、usage、费用和版本来源。
3. 技术详情：Job/Provider ID、hash、capability、Asset/SHA256、TOS publication 和幂等状态。

Pinia、URL 和浏览器存储只保存界面偏好、查询状态、临时草稿和 HTTP 结果未知时的幂等键；不得成为业务事实。刷新后，Job、候选、Prompt、项目选择和版本关系必须从 PostgreSQL 恢复。

## 9. 安全、媒体和秘密管理

- API 仅绑定数值 loopback；Host 只接受 `127.0.0.1` 和 `localhost`。
- 写请求要求同源、正确 Content-Type 和启动期 CSRF Token。
- `.env` 被 Git 忽略；不得在文档、日志、DTO 或测试输出中打印 Ark Key、数据库密码、TOS AK/SK。
- 所有受管目录必须是仓库内相对路径，`RuntimePaths` 拒绝绝对路径和 `..` 越界。
- 上传图片校验扩展名、MIME、文件头和 Pillow 解码。
- Provider 返回媒体经受限 URL 下载、大小检查、解码/ffprobe 和 SHA256 后才能落盘。
- TOS 发布对象使用确定性键、私有 Bucket 和短期签名；数据库不保存完整签名 URL。
- 媒体清理必须先 audit、备份、SHA256 校验和隔离，不能用目录前缀或通配符宽泛删除。

## 10. 本机开发与运行

### 10.1 首次安装

```powershell
uv sync --extra dev
npm install
npm run contracts
npm run build
```

本机复用已有 PostgreSQL，但使用独立数据库 `catflow_studio`：

```powershell
.\scripts\configure-existing-postgres.ps1
```

不要读取或输出 `.env` 的真实值；只在需要时核对变量是否存在。

### 10.2 启动和停止

```powershell
.\scripts\start-local.ps1
.\scripts\stop-local.ps1
```

跳过重复 Web 构建并不自动打开浏览器：

```powershell
.\scripts\start-local.ps1 -SkipWebBuild -NoBrowser
```

启动顺序是迁移数据库、启动 API、启动 Worker Supervisor，并等待 health、数据库、Worker 新鲜心跳、FFmpeg 和 ffprobe 全部就绪。日志位于 `var/logs`；进程状态位于 `var/work`。

### 10.3 质量门槛

```powershell
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check .
npm run test
npm run typecheck
npm run build
git diff --check
.\.venv\Scripts\alembic.exe -c services/api/alembic.ini current
```

`npm run build` 会先重新导出 OpenAPI 并生成 TypeScript 契约。测试中的 Ark 必须使用 SDK/HTTP mock 或固定响应 fixture，不能访问真实 Provider。

## 11. 2026-09-04 已核验运行状态

只读检查结果：

- PostgreSQL：可用。
- API：可用，端口 8877。
- Worker：`ready`，心跳新鲜，Supervisor 自动恢复已启用。
- FFmpeg/ffprobe：可用。
- Provider：Ark；规划、图片、视频和诊断模型配置完整。
- Ark Key：已配置；真实付费提交开关：已开启。
- 片段修复发布能力：可用，最多 1 个视频参考和 9 个图片参考。
- 数据库 migration：`0020_shot_plan_review_workflow (head)`。
- 项目库：3 个未归档项目，分别为“浇花”“寻找滚落线团”“雨天擦爪”。
- 自动化验证：后端 `198 passed`（1 个 Starlette/httpx 弃用警告）；前端 `20 files / 87 tests passed`；Ruff、Vue TypeScript、Vite production build 和 `git diff --check` 均通过。
- Git：开始交接前工作树干净，基线提交为 `c04bdb6`；本次仅新增/更新本文、根 README 和精确的 `.gitignore` 文档例外，尚未创建提交。

运行状态会随进程和用户操作变化；新会话开始时仍需重新只读核验。

## 12. “浇花”分镜恢复的精确状态

项目 ID：`6dfe96a6-4eb2-4859-aead-55ab579f6442`。

- 版本 1：`dee9f068-cb5e-4aba-9a5f-e8186e22661a`
  - revision 1；
  - `accepted`、active；
  - 因故事/角色/环境输入变化显示为历史输入。
- 版本 2：`6ab33f8f-394b-4f98-a1a0-05ee187de38b`
  - revision 2；
  - `candidate`、inactive；
  - 由已有 Job Payload 无付费恢复；
  - 仍需用户人工比较和采用，不能自动切换 active。
- 来源 Job：`548544d9-f72b-4498-bd7c-7119009ebee3`
  - 历史 Job 状态仍是 `failed`，用于保留当时的错误审计；
  - provider=`ark`，kind=`plan_shots`；
  - usage：input 3946、output 2471、total 6417；
  - billingStatus=`unpriced`；
  - 恢复没有创建新 Job、没有增加 usage、没有调用 Ark。

恢复候选有 2 个 warning、0 个 blocking：

1. 第 1 镜头有 4 条 `sound.objectEffects`，已全部保留；这属于声音密度建议，不阻止采用。
2. Provider 返回未知字段 `blocking_note="内嵌角色调度符合要求"`；原始内容保留在 Job 技术详情中，不进入正式 ShotSpec 或视频 Prompt。

任何后续会话都不得为了“验证恢复”再次调用 Ark。下一步若围绕该项目工作，应先让用户在页面比较版本 1 和版本 2，再由用户决定是否采用版本 2。

## 13. 已知边界与后续注意事项

- 当前三个项目的项目库投影均显示 `needs_attention`；“雨天擦爪”虽已完成，也仍存在需要处理的历史状态，应按具体 attentionReasons 分析，不能只看阶段。
- “浇花”恢复候选尚未采用，这是业务待办，不是系统失败。
- 历史 `failed` Job 在恢复成功后仍保持失败审计状态；UI 用“已有结果已恢复”解释，不应篡改历史状态。
- 后端完整测试仍有一个 Starlette/httpx TestClient 弃用警告；不影响功能，但可在依赖升级任务中处理。
- 旧 `docs/http-api.md`、`docs/workflows/complete-production.md` 等内容来自旧架构，包含已经删除的 Scene/Shot/creative workflow API。新增开发必须以当前 OpenAPI 和本文为准。
- 根目录 `.env` 含真实配置且被 Git 忽略；不要复制到新会话 Prompt、日志或文档。
- 任何数据库或媒体清理都必须使用维护命令的 audit → manifest hash → execute → quarantine 流程。
- 用户已明确偏好本项目任务不使用 subagent；后续会话应在主任务内完成，除非用户另行明确要求。

## 14. 新会话启动清单

新会话开始后先完成以下只读动作：

1. 阅读根目录 `AGENTS.md`（若存在）和本文全文。
2. 确认仓库路径、分支、`git status --short` 和最新提交，不重置或覆盖用户改动。
3. 读取 `/api/v1/runtime/bootstrap`，确认数据库、Worker 心跳、FFmpeg 和 Provider 准入状态。
4. 运行 `alembic current`，确认数据库仍在当前 head。
5. 若任务涉及“浇花”，先读取上节列出的两个分镜版本和来源 Job，不重新生成。
6. 若任务只要求分析或总结，保持只读；不要因为服务可用就触发真实 Ark。
7. 修改代码时遵守仓库原则：优先直接且有真实职责的 API，避免只转发参数的薄包装；使用 `rg` 搜索；用 `apply_patch` 修改；保留无关改动。
8. 完成实现后运行与风险相称的测试，最终至少执行 Ruff、前端测试、typecheck、build 和 `git diff --check`。

## 15. 常用事实定位

- FastAPI 路由：`services/api/src/catflow/interfaces/api.py`
- 应用服务和公共 DTO：`services/api/src/catflow/application/service.py`
- Director 结果归一化：`services/api/src/catflow/domain/director_results.py`
- 分镜业务模型：`services/api/src/catflow/domain/models.py`
- PostgreSQL ORM：`services/api/src/catflow/infrastructure/models.py`
- PostgreSQL 仓储：`services/api/src/catflow/infrastructure/postgres_repository.py`
- 项目库投影：`services/api/src/catflow/infrastructure/postgres_project_library.py`
- Worker 状态机：`services/worker/src/catflow_worker/runner.py`
- Worker 心跳/Supervisor：`services/worker/src/catflow_worker/lifecycle.py`
- Ark typed gateway：`services/worker/src/catflow_worker/ark_gateway.py`
- Ark Job 适配：`services/worker/src/catflow_worker/ark_job_gateway.py`
- Provider 结果落盘：`services/worker/src/catflow_worker/ark_results.py`
- 前端工作区：`apps/web/src/views/WorkspaceView.vue`
- 分镜页面：`apps/web/src/components/workspace/StoryboardStep.vue`
- 环境/资产页面：`apps/web/src/components/workspace/AssetsStep.vue`
- 局部修改页面：`apps/web/src/components/workspace/VideoRepairWorkspace.vue`
- 当前 OpenAPI：`packages/contracts/openapi.json`
- 生成的 TypeScript 契约：`packages/contracts/src/schema.d.ts`
