# 文档索引

当前系统为 Vue、FastAPI、PostgreSQL 和持久 Worker，以下文档对应现行代码。旧 V5 的 Scene/ShotCard 与 cvg 启动命令不适用于当前工程。

- [当前架构与开发交接](CURRENT_ARCHITECTURE_AND_HANDOFF.md)：三条生产路径、环境、Canon、冻结和恢复。
- [猫咪参考选择](CAT_REFERENCE_OPTIONS.md)：灰猫／V4 白猫、新建、导入、系列和重制。
- [清理、节拍与表演改进](PERFORMANCE_AND_CLEANUP.md)：本轮变更、验证和限制。
- [外部任务状态与恢复](JOB_EXECUTION_RECOVERY.md)：外部状态、原始回执和恢复边界。
- [双播放器与逐镜生产](DUAL_PLAYER_SCENE_PRODUCTION.md)：整片对照、单镜生成与组装。
- [视频候选试用](VIDEO_CANDIDATE_TRIALS.md)：选区、音轨和不可变候选；其中编译版本属于文档写作时记录。
- [提示词治理](PROMPT_GOVERNANCE.md)：系统规则、用户文字和来源数据的边界。
- [启动与维护](../README.md)：实际本机入口。
- [生成的 API 契约](../packages/contracts/openapi.json)：当前接口事实。

[历史档案](archive/2026-09-14/README.md)仅用于追溯。`VIDEO_EDIT_DRAFTS.md`、`LOCAL_VIDEO_EDIT.md` 保留早期编辑模型及验收证据；新开发不得用其中的旧时长限制覆盖当前 `VideoEditOptions` 契约。研究资料与历史白猫素材继续保留，不是运行时规则入口。
