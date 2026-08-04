# 见舟 · 自媒体运营工作台 v2.1

本地优先的个人创作者运营工作台。Vue 3（免构建）+ FastAPI + SQLite，单一启动入口，零配置直接运行。

## 快速开始

### 环境要求

- Python 3.11+（无需 Node.js，前端为免构建 Vue 3，已本地内置）

### 安装与启动

```bash
pip install -r requirements.txt
python app.py
```

启动后自动打开浏览器：<http://127.0.0.1:8741>

- 端口冲突：`set JIANZHOU_PORT=8800 && python app.py`（Linux/macOS 用 `export`）
- 不自动打开浏览器：`set JIANZHOU_NO_BROWSER=1`

> 若 pip 默认镜像不可用，可加 `-i https://pypi.org/simple`。

### 首次运行

- 自动创建 `data/app.db`（SQLite）并写入演示数据（创作者「林小舟」的完整运营世界）；
- 默认开启**显式演示模式**（`JIANZHOU_DEMO=1`）：所有外部数据来自内置演示 Provider，界面右上角显示「◆ 演示数据」徽标；
- 关闭演示模式：`set JIANZHOU_DEMO=0`。关闭后未配置真实 API Key 的功能将按「无静默兜底」原则显式报错（Typed Error + Trace ID）。

### 冒烟测试

服务运行中执行：

```bash
python scripts/smoke_test.py
```

37 项断言：完整业务链路 + 无兜底专项（未授权 ASR 不自动执行 / 预测锁定只读 / 非法状态转换拒绝 / 部分导入明示失败行 / 错误必带 Trace ID）。

## 功能地图（12 页面）

| 页面 | 回答的问题 |
|---|---|
| 工作总台 | 我现在最应该处理什么？ |
| IP档案 | 我是谁，我为谁创作，我通常怎么表达？ |
| 账号运营 | 我的账号现在是什么状态，这个周期怎么运营？ |
| 内容雷达 | 最近有什么内容值得我进一步看？ |
| 内容提取 | 我需要从这条内容中拿到哪些原始材料？ |
| 内容拆解 | 这条内容的可复用结构是什么？ |
| 创作工坊 | 这件事如何用我的身份、观点和语言讲出来？ |
| 内容计划 | 这周具体做哪些内容，什么时候做？ |
| 数据复盘 | 这条内容是否完成了它原本的目标，为什么？ |
| 成长记录 | 我的 IP 和运营方法是如何一步步形成的？ |
| 资料中心 | 系统正在使用哪些资料，它们从哪里来？ |
| 系统设置 | 数据源、模型、成本、诊断如何配置？ |

## 核心设计原则（已在代码中落实）

1. **无静默兜底**：外部失败 = 显式 Typed Error（错误码 + 阶段 + Trace ID），不返回假数据、不自动切换模型/适配器、不把失败任务标记为完成；
2. **先计划后执行**：提取/拆解等高成本动作先生成 ExecutionPlan（步骤 + 成本 + 产物），用户确认才创建 Job；
3. **显式授权**：「无字幕时继续 ASR」默认不勾选；未授权时任务停在「待你决策」，不自动进入 ASR；
4. **状态机驱动**：内容项目 10 主状态 + Job 状态机，非法转换被后端拒绝（CONTENT_STATE_INVALID）；
5. **不可覆盖**：原生字幕 / 原始 ASR 不可覆盖；发布前判断锁定后只读，仅能追加复盘；
6. **规则不自动生效**：规则建议必须人工二次确认后才写入规则经验包新版本；
7. **数据包隔离**：用户信息全部以数据包挂载，切换创作者 = 切换激活包组合 + 清空会话上下文，禁止跨用户检索；
8. **演示模式显式**：Mock 不是兜底——演示产物标注 demo 来源，可在设置中关闭。

## 主题

顶栏 ☾/☀ 一键切换浅色 / 深色主题，偏好持久化（localStorage + 设置同步），全部页面、弹层、图表双主题适配。

## 目录结构

```text
app.py                 # 单一启动入口
requirements.txt
app/                   # 后端（FastAPI）
  main.py              # 应用装配（API + 静态前端 + 错误体系 + Job Worker）
  config.py            # 配置与常量
  db.py                # SQLite 持久层（幂等建表）
  errors.py            # Typed Error 体系（Trace ID）
  seed.py              # 演示数据种子
  providers.py         # Demo / 真实适配器（无 Key 即 fail-fast）
  jobs.py              # Job 状态机 + asyncio Worker
  api/                 # 13 个领域路由
frontend/              # 前端（免构建 Vue 3，无 Node 依赖）
  index.html
  assets/css/app.css   # 设计令牌 + 深色/浅色双主题
  assets/js/           # store / api / 组件 / 12 页面
scripts/smoke_test.py  # 冒烟测试（37 项断言）
packaging/             # EXE 打包配置（预留，未执行）
docs/                  # 审核报告 + v2.1 修订说明 + 原型 v2.1
data/                  # 运行时生成（数据库 / 日志 / 备份）
```

## EXE 打包

配置已预留：`packaging/pyinstaller.spec` 与 `packaging/打包说明.md`。首版不执行打包。

## 文档

- `docs/审核报告_三文档一致性_v2.0.md`：PRD / 技术方案 / 原型三方审核（5 P0 + 10 P1 + 7 P2）
- `docs/PRD_v2.1_修订说明.md`、`docs/技术方案_v2.1_修订说明.md`：经确认的优化方案
- `docs/原型_v2.1.html`：修订后的产品原型
