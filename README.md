# CatFlow Web Studio

CatFlow 是本机单用户的一人一猫原创生活短片工作室。正式界面只有浏览器 Web 页面；FastAPI 在 `127.0.0.1:8877` 同源提供 Vue SPA、REST、SSE 和媒体内容，PostgreSQL 是唯一业务状态源，Python Worker 负责 durable planning、Ark 媒体和 FFmpeg 成片。

首版产品边界固定为：

- 一个项目对应一条独立的 8–15 秒生活短片。
- 9:16、720×1280、无对白或极少对白。
- 固定同一位 6–7 岁、约 1.2 米、约 4.5–5 头身的齐下颌短发儿童，以及同一只灰白虎斑猫。
- Canon v4 柔和数字插画；`style_source` 永不进入图片或视频 Provider 输入。
- 不使用 Electron、Toonflow-app、Express、SQLite、Socket.IO、Nginx 或第二业务状态源。
- 不复制 Sowii 的具体角色、画面、台词、故事或品牌元素，只采用“日常微事件、低对白、人猫互动、温暖结尾”的原创内容语法。

## 五步工作流

```text
生活灵感
  → 角色与画风（五个固定槽位）
  → 分镜画布（1–4 个镜头）
  → 生成与选择（冻结输入、幂等 Job）
  → 剪辑与导出（逐帧区间修复、WebAV 预览、FFmpeg 正式 MP4）
```

浏览器只保存当前项目、查询缓存、面板与 SSE 连接状态。故事、Canon、选择、分镜、Job、Provider task ID、剪辑版本和正式成片全部从 PostgreSQL 投影。

## 项目库

`/projects` 使用适合数百条短片的项目库：每个项目最多属于一个一级收藏夹，并可拥有最多 8 个主题标签；“最近更新、创作中、待处理、已完成、已固定、已归档”均由 PostgreSQL 中的真实故事、分镜、选择、任务和剪辑活动动态投影。项目 `theme` 继续保存创作描述，不被改造成目录。

项目库默认按最近活动日期分组，以 36 条为一页进行游标分页；搜索、收藏夹、标签交集、阶段、日期和排序在服务端完成，不会把数百条项目全部传给浏览器。网格用于快速浏览，管理列表支持批量移动、标签、固定、归档和恢复。归档不会删除媒体或历史，存在运行任务的项目会拒绝归档。

视频或最终成片落盘后，Worker 会幂等提取本地竖屏封面；旧资产可安全补齐：

```powershell
.\.venv\Scripts\catflow-worker.exe backfill-posters --limit 200
```

封面失败只会回退到环境图或柔和占位图，不会把已经成功的视频任务改成失败。

## 项目结构

```text
apps/web/                 Vue/Vite 唯一正式前端
assets/canon/v4/          四张可提交 Provider 的正式 Canon 生产包
services/api/             FastAPI 模块化单体与新 Alembic 基线
services/worker/          Durable Worker、Ark typed gateway 与 FFmpeg
packages/contracts/       OpenAPI 生成的 TypeScript 契约
scripts/                  本机配置、启动、停止及迁移脚本
tests/catflow/            新领域、仓储、Worker、媒体测试
tests/contract/           同源安全与 HTTP 契约测试
var/                      媒体、工作文件、日志和备份（Git 忽略）
风格定稿/                 历史设计源和审计材料，不直接进入 Provider
```

旧 `cat-video-generator` 业务包、前端、迁移链和历史测试已经从本仓库移除。当前构建、测试和运行入口只使用上面的 CatFlow 目录；旧 43 表 Schema 和旧 API 不会连接到 `catflow_studio`。

## PostgreSQL 配置

CatFlow 不启动 Docker PostgreSQL。它复用已经可用的 PostgreSQL 实例，但使用独立数据库：

```text
catflow_studio
```

从相邻旧项目安全提取现有连接参数：

```powershell
.\scripts\configure-existing-postgres.ps1
```

脚本只生成被 Git 忽略的 `.env`，把数据库名改为 `catflow_studio`，不会打印密码，也不会修改旧 `vedio-appdb`。当前实例中的 `catflow_studio` 使用独立 Alembic 迁移链；当前迁移 head 为 `0020_shot_plan_review_workflow`。数据库表和接口的现状汇总见 [当前架构与开发交接](docs/CURRENT_ARCHITECTURE_AND_HANDOFF.md)。

## 本机启动

首次安装或依赖变化后：

```powershell
uv sync --extra dev
npm install
npm run contracts
npm run build
```

日常启动：

```powershell
.\scripts\start-local.ps1
```

启动脚本不会检查或启动 Docker。它会：

1. 检查 `.env` 和正式端口占用。
2. 构建 Vue SPA。
3. 对现有 PostgreSQL 执行待应用的 Alembic migration。
4. 隐藏启动 FastAPI 与 Worker。
5. 等待 `/api/v1/health` 与 Worker/FFmpeg 就绪，成功后打开 `http://127.0.0.1:8877/projects`。
6. 失败时停止本次启动的进程，并保留 PostgreSQL、媒体、配置和备份。

停止：

```powershell
.\scripts\stop-local.ps1
```

如果端口 `8765` 仍被旧项目占用，启动脚本会报告 PID 并停止，不会擅自终止旧服务。

## 备份、恢复与维护清理

创建包含业务表和 `CATFLOW_MEDIA_ROOT` 所指相对媒体目录的本机备份：

```powershell
.\scripts\backup-local.ps1
```

恢复默认拒绝写入非空数据库；只有用户明确确认归档后才使用 `-Replace`：

```powershell
.\scripts\restore-local.ps1 -Archive .\var\backups\catflow-YYYYMMDD-HHMMSS.zip
.\scripts\restore-local.ps1 -Archive .\var\backups\catflow-YYYYMMDD-HHMMSS.zip -Replace
```

生产清理必须先生成只读计划，再使用计划哈希显式执行：

```powershell
.\.venv\Scripts\catflow.exe cleanup audit --output .\var\backups\cleanup-plan.json
.\.venv\Scripts\catflow.exe cleanup execute --manifest .\var\backups\cleanup-plan.json --manifest-sha256 <sha256>
```

执行命令会拒绝运行中的 API、Worker 和活跃任务；它先创建完整逻辑数据库/媒体备份，再逐文件校验并隔离待删媒体。隔离副本至少保留 7 天，`purge-quarantine` 会再次检查数据库引用后才删除清单中的精确文件。

### 遗留资产迁移（临时兼容）

旧数据导入器已收敛到 `scripts/legacy/`，只为尚未完成的一次性资产迁移保留，计划在下一次清理版本移除。它默认只做只读 dry-run：

```powershell
.\scripts\import-legacy-assets.ps1
.\scripts\import-legacy-assets.ps1 -Apply
```

导入器只读取旧库中 `approved` 的 Canon、已选角色设计、环境、视频和成片，按 SHA256 校验及去重后复制媒体。它不导入 Scene、ShotCard、Generation Plan、Production Recipe、Canvas、Review、Workflow Step 或 Provider 任务历史。`style_source` 即使被归档导入也会带 `providerEligible=false`，不会进入新生成请求。

## 安全与付费边界

- API 代码没有 `0.0.0.0` 启动选项，只绑定数值 loopback 地址。
- Host 只允许 `127.0.0.1` 和 `localhost`；正式环境不启用 CORS。
- 浏览器写请求必须同源、使用 `application/json`（上传除外）并携带启动期 CSRF Token。
- Provider Key、数据库凭据、磁盘路径和进程环境不进入 Renderer。
- 相同幂等键与输入返回同一个 Job；输入变化返回 `409`。
- Provider task ID 持久化后，Worker 重启只能继续轮询、存储、取消或对账。
- 正式 Ark 片段修复通过应用拥有的 S3 兼容发布器，把本地上下文 MP4 流式上传为私有临时对象，再向 Ark 提交 2 小时只读预签名 HTTPS URL。首发使用 TOS 北京区 S3 兼容 Endpoint、私有 Bucket `test-vedio-ylq` 和 VirtualHostStyle；后续切换 MinIO 只改对象存储配置，不改 Ark Gateway、Job 或 Repair 生命周期。2026-09-02 的一次性 Web 探针已确认 Seedance 会以 HTTP 400 拒绝 `data:video/mp4;base64,...`，正式流程不会再发送视频 Data URL。该探针报告已随本轮生产清理进入 7 天隔离备份，不再作为正式项目文档入口。
- 正式运行只使用 Ark；是否允许新的真实付费提交由 `.env` 中的 `CATFLOW_PAID_CALLS_ENABLED` 明确控制。Ark Key 未配置或开关关闭时，历史项目仍可查看、播放和本地导出，但所有模型生成入口都会拒绝提交；浏览器不会接收 Ark Key。
- 上传图片会校验扩展名、MIME、文件头和 Pillow 解码结果，媒体磁盘路径不直接暴露。
- 数据库只保存相对 `storage_key`；API、Worker、备份和恢复通过同一个 `RuntimePaths` 在项目根内解析，拒绝绝对路径与越界路径。

对象发布器使用 `CATFLOW_OBJECT_STORAGE_*` 命名空间。`.env.example` 包含 TOS 的非敏感默认配置，真实 AK/SK 只写在被 Git 忽略的 `.env` 中；旧 `AccessKeyId` / `SecretAccessKey` 仅保留一版兼容读取并产生弃用警告。设置页的“验证上传、签名读取与删除”会创建 1 KiB 随机对象、通过公网预签名 URL 读回并核对 SHA256，随后立即精确删除，不调用 Ark、不产生模型费用，也不会把签名 URL 返回浏览器。

发布记录保存在 PostgreSQL `media_publications`：每个 Repair Job 只有一个确定性对象键，数据库只记录 Bucket、对象键、源 SHA256、ETag、状态、到期时间和脱敏错误，不保存凭据或完整签名 URL。Worker 每分钟领取到期记录并执行精确 `DeleteObject`，删除失败保持 `delete_pending` 等待重试；对象默认保留 7 天。TOS 控制台还应为 `catflow/segment-references/` 前缀配置 7 天生命周期规则作为 Worker 停机兜底，应用不会修改全桶策略。

CatFlow 的 IAM 身份应只拥有 Bucket `test-vedio-ylq` 下 `catflow/segment-references/*` 和 `catflow/publisher-checks/*` 的 `PutObject`、`GetObject`、`HeadObject`、`DeleteObject`；仅当 TOS 探测要求时再增加最小 `HeadBucket` / `GetBucketLocation`。不要授予公开读、全桶管理、策略修改或 Bucket 删除权限。

## 质量门槛

```powershell
.\.venv\Scripts\pytest.exe tests\catflow tests\contract -q
.\.venv\Scripts\ruff.exe check services\api\src services\worker\src tests\catflow tests\contract
npm run contracts
npm --workspace apps/web run test
npm --workspace apps/web run typecheck
npm --workspace apps/web run build
git diff --check
```

媒体测试使用当前 `.env` 中已配置的 FFmpeg/ffprobe，并在临时媒体目录生成 720×1280、8–15 秒 MP4。所有测试都固定连接数据库名 `catflow_studio`，并只清理自己创建的精确项目 ID。

## 关键不变量

默认视频参考顺序不可由前端重排：

```text
本集儿童设计
→ 本集猫咪设计
→ 人猫同框比例
→ 当前环境参考
→ Canon v4 净化画风板
```

Provider 上限不足时按上述优先级裁剪并记录 `omittedReason`。`style_source` 即使有空余名额也不会进入请求。AI 质量诊断只提供 warning；人工选择和最终批准才改变当前投影。

## 逐帧片段修复

剪辑页以恒定 24 fps 和 `[startFrame, endFrame)` 表示正式区间。用户可通过 I/O 键、逐帧/十帧步进和时间线拖动选择 4–15 秒区间，左右边界不能越过源视频，也不能交叉。Provider 可以在合法问题区间两侧增加连续性上下文，但最终候选核心段始终与原问题区间等长。每次明确生成只创建一个 `regenerate_video_segment` Job，候选完成后不会自动替换。

批准候选必须完成七项质量检查和入/出点接缝检查。服务端随后基于当前时间线哈希创建新的不可变 `EditVersion`（EDL v2），原视频、旧 EditVersion、失败候选和 Provider task ID 全部保留。正式导出使用 FFmpeg 合成前段、批准的候选核心段和后段；默认硬切，可显式选择 2/4/6 帧叠化，输出总帧数保持不变，候选音轨永不进入正式成片，根视频无音轨时继续保持无音轨。
