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
  → 剪辑与导出（WebAV 决策、FFmpeg 正式 MP4）
```

浏览器只保存当前项目、查询缓存、面板与 SSE 连接状态。故事、Canon、选择、分镜、Job、Provider task ID、剪辑版本和正式成片全部从 PostgreSQL 投影。

## 项目结构

```text
apps/web/                 Vue/Vite 唯一正式前端
assets/canon/v4/          四张可提交 Provider 的正式 Canon 生产包
services/api/             FastAPI 模块化单体与新 Alembic 基线
services/worker/          Durable Worker、fake gateway 与 FFmpeg
packages/contracts/       OpenAPI 生成的 TypeScript 契约
scripts/                  本机配置、启动、停止及迁移脚本
tests/catflow/            新领域、仓储、Worker、媒体测试
tests/contract/           同源安全与 HTTP 契约测试
var/                      媒体、工作文件、日志和备份（Git 忽略）
风格定稿/                 历史设计源和审计材料，不直接进入 Provider
```

仓库根目录保留从 `cat-video-generator@43b1213` 复制来的旧 `src/`、`web/`、旧 Alembic 与历史测试，作为迁移参考。新构建、测试和运行入口只使用上面的 CatFlow 目录；旧 43 表 Schema 和旧 API 不会连接到 `catflow_studio`。

## PostgreSQL 配置

CatFlow 不启动 Docker PostgreSQL。它复用已经可用的 PostgreSQL 实例，但使用独立数据库：

```text
catflow_studio
```

从相邻旧项目安全提取现有连接参数：

```powershell
.\scripts\configure-existing-postgres.ps1
```

脚本只生成被 Git 忽略的 `.env`，把数据库名改为 `catflow_studio`，不会打印密码，也不会修改旧 `vedio-appdb`。当前实例中的 `catflow_studio` 已使用新 Alembic `0001_catflow_core` 建立 12 张业务表；`catflow.alembic_version` 是额外的迁移版本表。

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

## 备份、恢复与旧资产导入

创建包含业务表和 `CATFLOW_MEDIA_ROOT` 所指相对媒体目录的本机备份：

```powershell
.\scripts\backup-local.ps1
```

恢复默认拒绝写入非空数据库；只有用户明确确认归档后才使用 `-Replace`：

```powershell
.\scripts\restore-local.ps1 -Archive .\var\backups\catflow-YYYYMMDD-HHMMSS.zip
.\scripts\restore-local.ps1 -Archive .\var\backups\catflow-YYYYMMDD-HHMMSS.zip -Replace
```

旧数据导入器默认只做只读 dry-run：

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
- 当前 `CATFLOW_PROVIDER=fake`、`CATFLOW_PAID_CALLS_ENABLED=false`；本实现不会发起真实付费调用。
- 上传图片会校验扩展名、MIME、文件头和 Pillow 解码结果，媒体磁盘路径不直接暴露。
- 数据库只保存相对 `storage_key`；API、Worker、备份和恢复通过同一个 `RuntimePaths` 在项目根内解析，拒绝绝对路径与越界路径。

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
