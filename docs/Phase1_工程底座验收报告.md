# Phase 1 工程底座验收报告

验收日期：2026-08-04

## 已完成

- 建立 Git 修复前基线，运行数据库、日志、密钥与构建产物均被排除。
- 将完整 PRD v2.0 与技术方案 v2.0 纳入项目，并建立文档优先级和实现状态口径。
- 修复干净数据库首次启动时对标账号、作品占位符及演示种子外键顺序错误。
- 引入 schema v2 迁移、迁移校验和、关键索引、外键、事务、旧库原子重建和迁移前备份。
- 将新记录 ID 改为带业务前缀的 UUID。
- 开发模式使用项目 `data/`；EXE 默认使用 `%LOCALAPPDATA%/JianzhouMediaWorkbench/`；支持 `JIANZHOU_DATA_DIR` 覆盖。
- API Key 从 SQLite 迁移到 Windows DPAPI 存储，设置 API 只返回“是否已配置”。
- 修复真实数据源“只有 baseUrl 就显示已配置”的错误。
- 给真实 LLM Provider 补齐统一业务契约；未配置时返回 Typed Error。
- 阻止 Provider/LLM 请求访问未授权域名、本机或私有网络，并阻止自动重定向。
- 增加上传大小限制、本机监听限制、Origin 检查、安全响应头和轮转日志。
- 修复 PyInstaller 动态导入和项目根目录问题，增加无关依赖排除项。

## 验收结果

| 检查 | 结果 |
|---|---|
| Python compileall | 通过 |
| 前端 10 个 JavaScript 文件 `node --check` | 通过 |
| Phase 1 单元测试 | 6/6 通过 |
| 全空 Demo 数据库 | integrity=ok，foreign_key_errors=0，2 个对标账号 |
| 全空 Real 数据库 | 1 个最小用户，0 个演示对标账号 |
| Real 缺模型配置 | HTTP 400 + `CONFIG_MISSING_API_KEY` |
| 旧数据库迁移 | 用户/任务数据保留，schema v2，integrity=ok，foreign_key_errors=0 |
| 演示业务冒烟 | 38/38 通过 |
| PyInstaller 干净环境构建 | 通过，EXE 约 16.2 MB |
| EXE 实际启动 | healthz、首页、静态 JS、外部数据库与日志均通过 |

## 数据迁移

现有 `data/app.db` 已迁移到 schema v2。迁移前备份保存在：

`data/backups/app-20260804-192023-982885-pre_constraint_rebuild.db`

该备份包含业务数据，应按本地私密数据管理，不应提交到 Git 或外发。

## 尚未进入本阶段的工作

- 请求身份、对象级权限和完整多用户隔离。
- 可恢复 Job Engine、任务租约、幂等、持久化取消与步骤日志。
- 真实 Provider、LLM、STT 的带密钥端到端验证。
- 素材文件库、引用证据链、Context Resolver。
- 发布后 T+N 快照、真实复盘和规则包版本闭环。
- 对标监控前端接入和浏览器端可访问性验收。

这些内容属于 Phase 2 及后续垂直切片，不在本报告中宣称完成。
