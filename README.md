# Cat Video Generator

面向原创“一人一猫”治愈短视频的本地导演生产系统。产品采用 Toonflow 式任务结构：项目内只有剧本、角色资产和生产画布三个入口；视频生成、版本审核、局部编辑、时间线、合成与导出统一收敛到生产画布中的近全屏 Workbench。

```text
项目
├─ 剧本：完整故事候选、正文编辑、Revision 与当前剧情
├─ 角色资产：儿童 Canon、猫咪 Canon、本集设计、同框比例、环境与画风板
└─ 生产画布
   ├─ 剧本
   ├─ 导演计划
   ├─ 角色与素材
   ├─ 分镜表
   ├─ 分镜画面
   └─ 视频工作台
      ├─ 预览
      ├─ 视频生成
      └─ 剪辑与交付
```

## 生产安全内核

- PostgreSQL 是唯一工作流状态源；Vue Flow 位置只是展示布局，不替代业务关系。
- Story、Canon、Storyboard、媒体、视频和成片均使用不可变 Revision 与血缘。
- 剧情、Canon、分镜或参考变化会按正式依赖传播 stale，历史产物不会被覆盖。
- 人物与猫咪分别只有一个 Provider 权威身份来源；Canon v4 固定 8–9 岁短发儿童与灰白虎斑猫。
- `style_source` 只保留画风提炼血缘，不能提交日常 Provider；只有净化后的 `style_board` 可以进入人物、猫咪、环境和视频请求。
- 费用确认前冻结 Provider、模型、参数、有序参考、Prompt、Revision 与输入哈希。
- durable task、worker lease、幂等键和 Provider task ID 防止重复付费提交。
- 任务中心严格区分本地排队、正在提交、Provider 排队、Provider 运行和未知状态；`submission_unknown` 与 `cancellation_unknown` 只能对账，不能重试。
- 图片和视频由人工审核；艺术质量诊断只产生 warning，不代替真实安全策略或执行校验。

## 本地启动

```powershell
uv sync --extra dev
uv run cvg doctor

# 终端一
uv run cvg api

# 终端二
npm --prefix web install
npm --prefix web run dev -- --host 0.0.0.0
```

浏览器打开 `http://localhost:5173/projects`。单服务模式：

```powershell
npm --prefix web run build
uv run cvg api --static-dir web/dist
```

历史 `/canvas` 和 `/studio` 链接只负责规范化跳转到新页面，不再维护旧工作台。

## Web 使用方式

1. 在项目页创建原创一人一猫项目，选择 Canon v4、目标时长、9:16 画幅和质量档。项目、Brief、两个主体、Canon 引用与初始 Recipe 在同一事务创建，创建本身不调用 Provider。
2. 在剧本页生成 1–5 个完整故事候选，编辑长正文并设为当前剧情。正式修改创建新 Revision。
3. 在角色资产页检查唯一身份权威、净化画风板和真实媒体，先预览三组角色设计输入与费用，再生成本集儿童、猫咪和同框比例图。
4. 在生产画布编辑分镜标题、完整方向、时长、顺序和参考。完整分镜、角色档案与制作包不进入通用节点浮窗。
5. 从“视频工作台”产物打开近全屏 Workbench，同屏核对有序参考、专业自然语言 Prompt、Provider 参数、任务、结果与历史版本。
6. 在 Workbench 的“剪辑与交付”中完成局部编辑、时间线、转场、本地合成与导出。
7. 全局任务与系统设置位于应用左侧导航轨；项目内容加载失败不会隐藏外壳或其他项目入口。

## Prompt 与参考职责

Provider 创作 Prompt 只包含专业自然语言、职责清晰的有序参考和执行参数。数据库 ID、Revision、Hash、任务 ID 与原始 Schema 留在审计信息中，不拼入创作正文。

默认视频参考顺序：

```text
本集儿童设计
→ 本集猫咪设计
→ 人猫同框比例
→ 当前环境参考
→ Canon v4 净化画风板
```

基础 Canon 用于生成和审核本集权威设计，不与本集设计重复提交视频 Provider。叶片材质来源图永远不能出现在普通人物、猫咪、环境或视频请求中。

## 质量门槛

```powershell
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check .
npm --prefix web run test -- --run
npm --prefix web exec vue-tsc -- --noEmit
npm --prefix web run build
git diff --check
```

## 文档

- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [完整生产流程](docs/workflows/complete-production.md)
- [设计脚本教程适配说明](docs/workflows/tutorial-adaptation.md)
- [Windows 手册](docs/workflows/windows-runbook.md)
- [Docker Compose 部署](docs/workflows/docker-deployment.md)
- [HTTP API](docs/http-api.md)
- [架构收敛 Checklist](docs/checklists/v5-architecture-convergence.md)
- [专业视觉资产 Checklist](docs/checklists/v5-professional-visual-assets.md)
- [非阻塞媒体工作流 Checklist](docs/checklists/v5-nonblocking-media-workflow.md)

`.env`、Ark Key、数据库密码、Authorization、Cookie、Base64 和完整签名 URL 不得进入 Git、日志或诊断 Manifest。
