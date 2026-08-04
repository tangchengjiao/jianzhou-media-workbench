"""演示数据种子：仅在数据库为空时写入。

所有演示数据 provider 标记为 demo_*，前端据此举「演示数据」徽标。
时间以当前日期为基准相对生成，避免硬编码。
"""
import datetime
import uuid

from .config import DEFAULT_SETTINGS
from .db import execute, jdumps, table_empty
from .errors import new_trace_id

TODAY = datetime.date.today()


def d(offset: int = 0) -> str:
    return (TODAY + datetime.timedelta(days=offset)).isoformat()


def ts(offset_days: int = 0, hour: int = 10) -> str:
    dt = datetime.datetime.combine(TODAY + datetime.timedelta(days=offset_days),
                                   datetime.time(hour, 0))
    return dt.isoformat(timespec="seconds")


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def seed_if_empty() -> None:
    if not table_empty("users"):
        return
    _seed_users()
    _seed_packages()
    _seed_profile()
    _seed_accounts()
    _seed_benchmarks()          # 对标账号 + 真实扫描（信号由扫描产生）
    _seed_signals_and_contents()
    _seed_analysis()
    _seed_creation()
    _seed_planning()
    _seed_retro()
    _seed_growth()
    _seed_corpus_and_imports()
    _seed_settings_and_usage()
    _seed_jobs()


def _seed_benchmarks() -> None:
    """通过真实扫描链路写入对标账号：作品、爆款判定、信号、消息全部真实产生。"""
    from . import benchmarks as bm_svc
    from .db import query_one

    bm1 = bm_svc.add_benchmark("u-zhou", "https://www.douyin.com/user/demo_yunyin6",
                               "douyin", "30", "核心对标 · 汇报表达赛道")
    bm2 = bm_svc.add_benchmark("u-zhou", "https://www.xiaohongshu.com/user/demo_cuicui",
                               "xhs", "30", "管理类头部")
    # 让第一个账号的最高分作品与既有拆解链路（SC-118 / A-1024）对上
    top = query_one(
        "SELECT * FROM benchmark_works WHERE bm_id=? ORDER BY score DESC LIMIT 1",
        (bm1["id"],))
    if top:
        execute("UPDATE benchmark_works SET title=? WHERE id=?",
                ("为什么你的汇报总被打断", top["id"]))
        sig = query_one("SELECT id FROM signals WHERE id LIKE ?",
                        (f"%-{top['video_id'][-6:]}",))
        if sig:
            execute("UPDATE signals SET title=?, relevance=92, rel_perf=? WHERE id=?",
                    ("为什么你的汇报总被打断", "账号均值 5.3×", sig["id"]))
            execute("UPDATE source_contents SET signal_id=? WHERE id='SC-118'",
                    (sig["id"],))
            globals()["_DEMO_TOP_SIGNAL"] = sig["id"]
            globals()["_DEMO_TOP_WORK"] = top["video_id"]
            globals()["_DEMO_BM1"] = bm1["id"]


def _seed_users() -> None:
    execute("INSERT INTO users VALUES(?,?,?,?)",
            ("u-zhou", "林小舟", "舟", ts(-140)))
    execute("INSERT INTO users VALUES(?,?,?,?)",
            ("u-tea", "陈早茶", "茶", ts(-30)))


def _seed_packages() -> None:
    packs = [
        ("pkg-ip", "ip_profile", "IP资料包", "v3.2", 12, "定位 / 观点 / 表达风格 / 禁用表达"),
        ("pkg-account", "account", "账号数据包", "v1.8", 4, "4 个自有账号 · 快照每日"),
        ("pkg-bench", "benchmark", "对标数据包", "v2.1", 8, "8 个对标账号 · 关键词 5 组"),
        ("pkg-corpus", "corpus", "内容语料包", "v1.5", 72, "72 份语料 · 授权分级"),
        ("pkg-feedback", "feedback", "用户反馈包", "v1.2", 1204, "评论 1,204 条 · 聚类 11"),
        ("pkg-rule", "rule", "规则经验包", "v2.0", 9, "规则 9 条 · 3 条待审核"),
        ("pkg-cycle", "operation_cycle", "运营周期包", "2026 Q3", 1, "不可变版本 · 稳定更新期"),
    ]
    for pid, ptype, name, ver, cnt, summary in packs:
        execute(
            "INSERT INTO data_packages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, "u-zhou", ptype, name, ver, "active", cnt, summary,
             "private", ts(-90), d(-2), ts(-90)))


def _seed_profile() -> None:
    content = {
        "ip_name": "林小舟",
        "positioning": "帮工作 3-8 年的职场人，把经验沉淀成可复用的方法论",
        "audience": "25-35 岁，一线/新一线城市的运营、产品、市场从业者",
        "value": "不讲鸡汤，只给可验证的方法与清单",
        "pillars": ["向上管理", "汇报表达", "职业选择", "个人工作流"],
        "key_opinion": "职场安全感来自可迁移能力，而不是司龄。",
        "monetization": "职场方法论专栏 + 1v1 咨询",
        "banned_words": ["家人们", "宝子们"],
        "field_sources": {
            "positioning": {"src": "访谈 #7 · 第 12 轮 14:32",
                            "quote": "我不教入门，我帮已经工作几年的人把经验变成方法。"},
            "audience": {"src": "访谈 #3 · 第 5 轮",
                         "quote": "看我内容最多的是 25 到 35 岁，一二线城市做运营、产品、市场的。"},
            "pillars": {"src": "复盘 #12 + 档案 v3.2 变更",
                        "quote": "新增「个人工作流」：近 8 周该方向互动率高于均值 31%"},
        },
    }
    execute("INSERT INTO profile_versions VALUES(?,?,?,?,?,?,?,?)",
            ("pv-3.2", "u-zhou", "v3.2", jdumps(content),
             "新增内容支柱「个人工作流」", "复盘 #12 → 规则建议 R-44", d(-14), 1))
    execute("INSERT INTO profile_versions VALUES(?,?,?,?,?,?,?,?)",
            ("pv-3.1", "u-zhou", "v3.1", jdumps({**content, "pillars": content["pillars"][:3]}),
             "修正目标用户年龄段", "反馈包聚类 #FC-9（n=412 条评论）", d(-40), 0))
    execute("INSERT INTO profile_versions VALUES(?,?,?,?,?,?,?,?)",
            ("pv-3.0", "u-zhou", "v3.0", jdumps({**content, "pillars": content["pillars"][:3]}),
             "首次语音建档", "访谈 #1 · 完整转写 42 轮 · 字段证据 38 条", d(-78), 0))

    cands = [
        ("fc-1", "禁用表达", "「家人们」「宝子们」", "explicit", 0.95,
         "访谈 #8 · 第 3 轮 · “我从来不说家人们，太油了。”", "pending"),
        ("fc-2", "自我称呼", "「我 / 小舟」，自称「小舟」频率约 23%", "inferred", 0.6,
         "语料包统计 42 份脚本 · 推断类型 inferred · 需人工确认", "pending"),
    ]
    for cid, path, val, itype, conf, ev, status in cands:
        execute("INSERT INTO profile_field_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, "u-zhou", "iv-8", path, val, itype, conf, ev, status, ts(-2)))

    # 暂停中的访谈会话 #8
    execute("INSERT INTO interview_sessions VALUES(?,?,?,?,?,?,?,?,?)",
            ("iv-8", "u-zhou", "表达风格", "voice", "PAUSED", 5, 12, ts(-3), ts(-2)))
    turns = [
        (1, "assistant", "我们聊聊表达风格。你写完一条稿子，通读时最先改的是什么？"),
        (2, "user", "先砍形容词。我稿子初稿特别啰嗦，第一遍基本都在删。"),
        (3, "assistant", "删完之后句长大概多少？能给一个你最近改过的例子吗？"),
        (4, "user", "基本都压到 15 字以内。比如「本质上来说职场沟通的核心」我直接改成「说白了，职场沟通就一件事」。"),
        (5, "assistant", "你写完一条稿子，通常会删掉什么？"),
    ]
    for idx, role, text in turns:
        execute("INSERT INTO interview_turns VALUES(?,?,?,?,?,?,?)",
                (uid("it"), "iv-8", idx, role, text, None, ts(-2, 14)))


def _seed_accounts() -> None:
    accs = [
        ("acc-dy", "抖音", "@林小舟", "own", "ok", "每 6 小时", 0, 86432, "tikhub",
         "重点平台", {"bound": True}),
        ("acc-xhs", "小红书", "@林小舟", "own", "error", "每 6 小时", -1, 34210, "tikhub",
         "TIKHUB_UNAUTHORIZED · API Key 已失效（401）· 连续失败 3 次",
         {"bound": True, "error_code": "TIKHUB_UNAUTHORIZED",
          "trace_id": new_trace_id()}),
        ("acc-bili", "B站", "@林小舟的方法论", "own", "manual", "手动导入", -5, 12340, "manual",
         "上次导入 5 天前（CSV · 后台导出）", {"bound": False}),
        ("acc-gzh", "公众号", "小舟方法论", "own", "manual", "手动导入", -12, 8921, "manual",
         "上次导入 12 天前", {"bound": False}),
    ]
    for aid, platform, handle, kind, status, freq, last, followers, bp, note, extra in accs:
        last_sync = ts(last, 8) if status != "manual" else None
        execute("INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, "u-zhou", platform, handle, kind, status, freq, last_sync,
                 followers, bp, note, jdumps(extra)))
    # 抖音近 12 周快照
    base = 82200
    for i in range(12, 0, -1):
        base += 350 + (i % 3) * 40
        execute("INSERT INTO account_snapshots VALUES(?,?,?,?,?,?)",
                (uid("ss"), "acc-dy", d(-i * 7), base, 4,
                 jdumps({"plays_avg": 52000 + i * 300, "completion": 0.41})))


def _seed_signals_and_contents() -> None:
    signals = [
        ("SG-2036", "关键词", "抖音 · 小红书", "监控词「向上管理」", "“向上管理”搜索热度 7 日上升 38%",
         "", -1,
         {"note": "关联新作品 47 条 · 其中 6 条高于各自账号均值 3×"},
         "", 88, "7 日趋势", "observed", "kw", "demo_keyword"),
        ("SG-2038", "用户反馈", "来自你的评论区", "近 5 条作品", "“如何选择 offer”类提问重复出现 11 次",
         "", -3,
         {"quote": "两个 offer 钱差不多，一个大厂打杂一个小公司核心岗，怎么选？"},
         "", 90, "持续出现", "new", "fb", "demo_feedback"),
        ("SG-2040", "历史起量", "你的作品", "发布于 45 天前", "《周报怎么写，领导才会真的看》近 3 日播放回升 210%",
         "", -45,
         {"new_plays": "4.2万", "new_collects": 1832, "search_ratio": "61%"},
         "", 95, "回升中", "new", "revive", "demo_snapshot"),
        ("SG-2042", "手动收藏", "B站", "@Agent老王", "30 岁前必须懂的 3 个职场真相",
         "https://example.com/p/2042", -3,
         {"note": "提取完成 ✓ 原生字幕 + 清洗文案已分层保存"},
         "", 70, "已处理", "processed", "manual", "demo_manual"),
    ]
    for sid, stype, platform, author, title, url, pub, metrics, rel, relev, timey, status, cat, src in signals:
        content_id = "SC-121" if sid == "SG-2042" else None
        execute(
            "INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, "u-zhou", stype, platform, author, title, url, d(pub),
             jdumps(metrics), rel, relev, timey, status, cat, src,
             ts(0, 9), content_id))

    # 来源内容资产：SC-121（提取成功，命中原生字幕）/ SC-118（待确认）
    assets_121 = {
        "metadata": {"exists": True, "file": "metadata.json"},
        "media": {"exists": False, "note": "未下载（命中字幕）"},
        "caption": {"exists": True, "immutable": True,
                    "trace": "demo_tikhub 字幕接口 · req_8af2 · ¥0.10 · SHA256 缓存键已记录",
                    "text": "三个职场真相：第一，不要把平台当能力……（演示字幕文本）"},
        "asr": {"exists": False, "note": "未执行（命中原生字幕，未触发 ASR）"},
        "clean": {"exists": True, "version": "v1",
                  "trace": "结构化模型清洗 · prompt_v3 · 输入哈希已记录",
                  "text": "职场真相清洗稿：别把平台当能力；别把忙碌当产出；别把听话当可靠。每一条都展开……（演示清洗文案）"},
        "comments": {"exists": False},
    }
    execute("INSERT INTO source_contents VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("SC-121", "u-zhou", "SG-2042", "30 岁前必须懂的 3 个职场真相", "B站",
             "@Agent老王", "https://example.com/p/2042", "transcribed",
             jdumps(assets_121), ts(-2)))
    top_signal = globals().get("_DEMO_TOP_SIGNAL", "")
    top_work = globals().get("_DEMO_TOP_WORK", "")
    execute("INSERT INTO source_contents VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("SC-118", "u-zhou", top_signal, "为什么你的汇报总被打断", "抖音",
             "@运营第6年", f"https://example.com/video/{top_work}", "metadata_only",
             jdumps({"metadata": {"exists": True},
                     "benchmark_work_id": top_work}), ts(-1)))


def _seed_analysis() -> None:
    result = {
        "topic": "汇报被打断的本质不是内容太多，而是结构顺序错误",
        "target_audience": ["需频繁向上汇报的职场中层"],
        "user_problem": "汇报没重点，领导听不下去",
        "hook": {"text": "你汇报被打断，不是领导没耐心。", "cite": "00:00-00:08",
                 "mechanism": "反常识归因"},
        "structure": [
            {"name": "钩子", "span": "0-8s", "weight": 8, "color": "#E4572E"},
            {"name": "痛点共鸣", "span": "8-30s", "weight": 22, "color": "#F08A4B"},
            {"name": "归因翻转", "span": "30-50s", "weight": 20, "color": "#D97706"},
            {"name": "方法三步", "span": "50-120s", "weight": 70, "color": "#2E6FDB"},
            {"name": "案例", "span": "120-150s", "weight": 30, "color": "#6E56CF"},
            {"name": "CTA", "span": "150-170s", "weight": 20, "color": "#57564F"},
        ],
        "key_turns": [{"text": "问题不在你讲太多，而在顺序错了。", "cite": "00:47"}],
        "evidence": ["2 个匿名真实汇报场景（口述案例，无数据引用）"],
        "emotional_mechanisms": ["焦虑 → 释然 → 掌控感"],
        "golden_quotes": [{"text": "汇报不是邀功，是降低领导的决策成本。", "cite": "01:58"}],
        "cta": "评论区领取汇报模板 + 提问“你遇到过哪种打断”",
        "comment_triggers": ["提问式收尾", "模板钩子"],
        "reusable": ["归因翻转结构", "模板型 CTA", "痛点具象化写法"],
        "non_reusable": ["对方个人案例与语气词", "“3 步法”逐字框架（与你既有内容重复度 41%）"],
        "source_chain": ["雷达 #SG-2031", "提取 #EX-118", "拆解 #A-1024"],
        "model": "demo_analysis_model", "prompt_version": "analysis_v5",
    }
    execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
            ("A-1024", "u-zhou", "SC-118", "done", jdumps(result),
             "demo_analysis_model", 0.35, ts(-1)))

    patterns = [
        ("pt-1", "归因翻转", "verified", 5, "中高", "适合当前 IP ✓",
         "口播/图文 · 本人已验证 ×2"),
        ("pt-2", "清单体三步法", "pending", 8, "中", "与历史内容重复风险 41%",
         "来源样本 8 · 待验证"),
        ("pt-3", "模板型 CTA", "verified", 4, "高", "收藏率提升显著",
         "评论区领资料 · 本人已验证 ×3"),
    ]
    for pid, name, status, samples, conf, fit, note in patterns:
        execute("INSERT INTO patterns VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, "u-zhou", name, status, samples, conf, fit, note, ts(-20)))


def _seed_creation() -> None:
    draft = """你汇报被打断，先别怪领导没耐心——大概率是你把顺序讲反了。

我上个月接一个咨询，姑娘准备了 12 页 PPT，从项目背景开始讲。讲到第 3 页，领导说：所以你到底要说什么？她当场就懵了。

说白了，领导听汇报，脑子里只有三件事：结论是什么、风险是什么、要我做什么。你按这个顺序讲，30 秒就能过。

我给你改一版你品一下：原来她开头是「这个项目起源于去年 Q3……」，改成「这次活动缺口 8 万预算，我需要你拍板两个方案，30 秒讲完」。

同一件事，顺序换了，结果完全不同。

最后给你一张汇报顺序清单：① 结论先行 ② 风险前置 ③ 诉求明确 ④ 背景放最后。评论区扣「顺序」，模板发你。"""
    ctx = {
        "packages": ["IP资料包 v3.2", "表达画像 v1.4", "规则经验包 v2.0", "运营周期包 Q3"],
        "sources": ["来源内容 ×1（SC-118）", "本人案例 ×2（咨询记录 #C-31 #C-42）"],
        "tokens": 6800, "isolation": "禁止跨用户检索 · 引用 ID 已记录",
    }
    checks = {
        "style_match": {"label": "风格适配 · 表达画像 v1.4", "status": "ok", "value": "匹配 92%"},
        "style_rules": {"label": "句长 / 禁用词 / 开头习惯", "status": "ok", "value": "✓ ✓ ✓"},
        "source_dup": {"label": "与来源内容长句重复", "status": "warn", "value": "2 处待处理"},
        "history_dup": {"label": "与本人历史内容重复", "status": "ok", "value": "0 处"},
        "fact_density": {"label": "本人事实与观点密度", "status": "ok", "value": "充分"},
        "note": "原创性检查仅提示风险，不做法律结论。2 处重复集中在「方法三步」的表述，建议替换为你的清单体。",
    }
    outline = ["① 钩子：被打断是顺序问题", "② 我的咨询翻车案例",
               "③ 正确顺序：结论-风险-诉求", "④ 对照改写演示", "⑤ 清单 + 模板 CTA"]
    execute("INSERT INTO creation_projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("CP-96", "u-zhou", "汇报被打断？先改顺序",
             "收藏率 > 账号均值 · 涨粉", "✓ 内容支柱「汇报表达」 · 目标用户吻合",
             jdumps(["拆解 #A-1024", "选题 #T-96"]),
             "同结构 · 不同案例：用我自己的 3 个咨询翻车案例（归因翻转结构，案例来自咨询记录 #C-31 #C-42）",
             jdumps(outline), jdumps(ctx), jdumps(checks), "DRAFTING", ts(-2)))
    execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
            (uid("dv"), "CP-96", 1, "抖音口播", "（初稿，已由风格适配与案例替换迭代）",
             "初稿生成", ts(-2)))
    execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
            (uid("dv"), "CP-96", 2, "抖音口播", "（风格适配口语化版本）",
             "风格适配（口语化）", ts(-1)))
    execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
            (uid("dv"), "CP-96", 3, "抖音口播", draft, "替换案例 2 · 当前", ts(-1)))
    execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("pred-96", "CP-96", "收藏率 > 账号均值 · 涨粉", "高于均值 1.2–1.8×",
             0.62, "真实案例密度高", "选题近期同质化",
             "归因翻转结构对我的受众同样有效", "案例 3 可能太具体，但值得试。", 0, None))

    # 另一个写作中项目
    execute("INSERT INTO creation_projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("CP-97", "u-zhou", "向上管理不是讨好", "互动率 > 均值",
             "✓ 内容支柱「向上管理」", jdumps(["选题 #T-92"]),
             "同问题 · 不同观点", jdumps(["① 立场先行", "② 反例", "③ 方法"]),
             jdumps(ctx), jdumps(checks), "DRAFTING", ts(-4)))
    execute("INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?)",
            (uid("dv"), "CP-97", 3, "图文", "（向上管理不是讨好 · v3，风格检查中）",
             "风格检查", ts(-1)))


def _seed_planning() -> None:
    tasks = [
        ("CT-1", None, "offer 二选一怎么选", "CANDIDATE", "抖音", d(3), 1.5, "mid",
         [{"t": "反馈", "c": "b-warn"}]),
        ("CT-2", None, "述职季：述职报告框架", "CANDIDATE", "小红书", d(4), 2.0, "mid",
         [{"t": "起量延伸", "c": "b-ok"}]),
        ("CT-3", None, "向上管理关键词延伸", "CANDIDATE", "", d(5), 1.5, "low",
         [{"t": "关键词", "c": "b-info"}]),
        ("CT-4", None, "工作五年的“偷懒”哲学", "CANDIDATE", "", d(6), 2.0, "low",
         [{"t": "对标", "c": "b-info"}]),
        ("CT-5", "CP-96", "汇报被打断？先改顺序", "SELECTED", "抖音口播", d(0), 2.0, "high",
         [{"t": "本周", "c": "b-pri"}]),
        ("CT-6", "CP-97", "向上管理不是讨好", "DRAFTING", "图文", d(5), 1.5, "mid",
         [{"t": "风格检查", "c": "b-warn"}]),
        ("CT-7", None, "汇报顺序清单图文版", "PRODUCING", "小红书", d(1), 1.5, "mid", []),
        ("CT-8", None, "年中述职前的 3 个准备", "READY_TO_PUBLISH", "小红书", d(2), 0.5, "high",
         [{"t": "已锁定判断", "c": "b-ok"}]),
        ("CT-9", None, "我劝你别急着做副业", "WAITING_RETRO", "抖音", d(-7), 0, "high",
         [{"t": "已到时间", "c": "b-warn"}]),
        ("CT-10", None, "5 个高效工具", "WAITING_RETRO", "抖音", d(-14), 0, "mid",
         [{"t": "低于均值", "c": "b-err"}]),
        ("CT-11", None, "周报怎么写", "PUBLISHED", "抖音", d(-45), 0, "mid",
         [{"t": "回升中 📈", "c": "b-ok"}]),
    ]
    for tid, proj, title, status, platform, date_, est, pri, badges in tasks:
        execute("INSERT INTO content_tasks VALUES(?,?,?,?,?,?,?,?,?,?)",
                (tid, "u-zhou", proj, title, status, platform, date_, est, pri,
                 jdumps(badges)))

    # 发布记录 + 快照（副业）
    execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("P-201", "u-zhou", None, "CT-9", "抖音", "https://example.com/pub/201",
             ts(-7, 19), "我劝你别急着做副业", "", "pred-201"))
    execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("pred-201", "", "互动率 > 均值 · 验证反焦虑立场", "高于均值 1.2–1.8×",
             0.62, "评论区真实痛点", "开头可能太冲",
             "反鸡汤立场在副业话题有效", "开头可能太冲，但立场要坚持。", 1, ts(-7, 18)))
    snaps = [("T+1", 12000, 820, 96, 640, -6), ("T+3", 31000, 2400, 288, 1980, -4),
             ("T+7", 58000, 5100, 402, 4814, 0)]
    for tn, plays, likes, comments, collects, off in snaps:
        execute("INSERT INTO publication_snapshots VALUES(?,?,?,?,?,?,?,?)",
                (uid("sn"), "P-201", tn, plays, likes, comments, collects, ts(off, 8)))


def _seed_retro() -> None:
    report = {
        "why": "评论重复提问 + 支柱「职业选择」 · 使用模式：归因翻转",
        "source_chain": ["反馈 #FC-8", "选题 #T-88", "发布 #P-201"],
        "rows": [
            {"metric": "相对表现", "expected": "高于均值 1.2–1.8× · 置信度 0.62",
             "actual": "均值 2.1×", "verdict": "超预期", "verdict_class": "b-ok"},
            {"metric": "收藏率", "expected": "> 6%", "actual": "8.3%",
             "verdict": "达成", "verdict_class": "b-ok"},
            {"metric": "验证假设", "expected": "“反鸡汤立场在副业话题有效”",
             "actual": "高赞评论：“终于有人不说搞钱焦虑了”（赞 2,140）",
             "verdict": "被验证", "verdict_class": "b-ok"},
            {"metric": "我的判断", "expected": "“开头可能太冲，但立场要坚持。”",
             "actual": "完播率高于均值 22%", "verdict": "部分修正",
             "verdict_class": "b-info"},
        ],
        "next": "将「反焦虑立场」纳入可复用模式 → 见规则建议 R-51。",
    }
    execute("INSERT INTO retro_reports VALUES(?,?,?,?,?)",
            ("RR-201", "u-zhou", "P-201", jdumps(report), ts(0, 8)))

    suggestions = [
        ("RS-51", "create", "副业/搞钱类话题，反焦虑立场互动更高",
         "发布 #P-201（均值 2.1×）、#P-187（均值 1.6×） · n=2", 0.7,
         "选题评估与文案立场", "创作工坊 · 选题评估 / 立场建议（不修改历史稿件）",
         "pending"),
        ("RS-52", "update", "模板型 CTA 置信度 0.8 → 0.9",
         "近 5 条使用内容收藏率均高于均值", 0.9, "结尾生成策略",
         "影响范围：结尾生成策略", "pending"),
        ("RS-53", "deprecate", "“周五晚发布效果最好”近 6 周不再显著",
         "发布 #P-190~#P-201", 0.4, "排期建议",
         "建议标记失效而非删除（保留历史）", "pending"),
    ]
    for rid, action, text, ev, conf, scope, impact, status in suggestions:
        execute("INSERT INTO rule_suggestions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (rid, "u-zhou", action, text, ev, conf, scope, impact, status, ts(-1)))

    rules = [
        ("R-44", "归因翻转结构适合我的受众", "verified", "全平台", 0.8, 2, "已验证 ×2"),
        ("R-45", "模板型 CTA 提升收藏率", "verified", "全平台", 0.8, 3, "已验证 ×3"),
        ("R-46", "反焦虑立场（副业话题）", "pending_review", "选题立场", 0.7, 2, "待审核"),
        ("R-47", "清单体 5 项优于 3 项", "pending", "写作结构", 0.5, 8, "待验证"),
        ("R-48", "“周五晚发布效果最好”", "deprecated", "排期", 0.3, 6, "已失效"),
        ("R-49", "“标题数字化必涨”", "not_applicable", "标题", 0.2, 4, "不适用我"),
        ("R-50", "评论区自问自答提升互动", "verified", "仅小红书", 0.7, 3, "仅小红书"),
        ("R-51", "高频日更起量", "verified", "仅起号期", 0.6, 5, "仅起号期"),
    ]
    for rid, text, status, scope, conf, cnt, note in rules:
        execute("INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, "u-zhou", text, status, scope, conf, cnt, note, "v2.0"))

    experiments = [
        ("EX-1", "开头钩子 A/B", "反常识归因 vs 痛点提问", f"{d(-26)} ~ {d(1)}",
         "3 秒留存", "6 条", "running", ""),
        ("EX-2", "清单体密度", "每条清单项数 3 → 5", f"{d(-48)} ~ {d(-28)}",
         "收藏率", "8 条", "done", "已结束 · 无显著差异"),
    ]
    for eid, name, change, period, metric, samples, status, result in experiments:
        execute("INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?,?)",
                (eid, "u-zhou", name, change, period, metric, samples, status, result))


def _seed_growth() -> None:
    events = [
        (d(-14), "IP档案 v3.2", "新增内容支柱「个人工作流」 · 来源：复盘 #12，经你确认后生效", "normal"),
        (d(-22), "高表现 · 《我劝你别急着做副业》", "均值 2.1× · 验证假设「反焦虑立场」 · 已沉淀为规则", "green"),
        (d(-34), "重要失败 · 《5 个高效工具》", "低于均值 60% · 教训：纯工具清单与「方法论」定位不符 · 已记录", "gray"),
        (d(-42), "规则新增 · “模板型 CTA 提升收藏”", "证据 3 条后由你确认 · 当前置信度 0.8", "green"),
        (d(-60), "运营阶段变更", "起号期 → 稳定更新期 · 周目标 3 条 → 4 条", "normal"),
        (d(-78), "IP档案 v3.0 · 首次语音建档", "42 轮访谈 · 38 条字段证据 · 全部经逐项确认", "normal"),
    ]
    for date_, title, desc, kind in events:
        execute("INSERT INTO growth_events VALUES(?,?,?,?,?,?)",
                (uid("ge"), "u-zhou", date_, title, desc, kind))


def _seed_corpus_and_imports() -> None:
    corpus = [
        ("cp-1", "历史脚本", "脚本", 42, {"style": 1, "fact": 1, "generate": 1}, "", "手动上传", 1),
        ("cp-2", "公众号文章", "文章", 18, {"style": 1, "fact": 1, "generate": 0}, "", "ZIP 导入 #IMP-1980", 1),
        ("cp-3", "直播转写", "转写", 6, {"style": 1, "fact": 0, "generate": 0}, "", "手动上传", 1),
        ("cp-4", "课程讲义", "讲义", 3, {"style": 0, "fact": 1, "generate": 0}, "", "手动上传", 1),
        ("cp-5", "客户咨询案例", "案例", 3, {"style": 0, "fact": 1, "generate": 1}, "2026-12", "手动上传", 1),
    ]
    for cid, name, ctype, cnt, perms, valid, source, analyzed in corpus:
        execute("INSERT INTO corpus_items VALUES(?,?,?,?,?,?,?,?,?)",
                (cid, "u-zhou", name, ctype, cnt, jdumps(perms), valid, source, analyzed))

    errors = [
        {"row": 7, "error": "日期格式无法解析 “5.32”", "action": "已跳过（未写入）"},
        {"row": 22, "error": "缺少必填字段 title", "action": "已跳过（未写入）"},
    ]
    mapping = [
        {"from": "笔记标题", "to": "title", "policy": "必填"},
        {"from": "发布时间", "to": "published_at", "policy": "格式 YYYY/M/D"},
        {"from": "点赞数", "to": "metrics.likes", "policy": "数值化"},
        {"from": "笔记链接", "to": "url", "policy": "去重键"},
    ]
    execute("INSERT INTO import_batches VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("IMP-2041", "u-zhou", "小红书后台导出.csv", "账号数据包",
             "partial", 40, 38, 2, jdumps(errors), jdumps(mapping), ts(-1)))


def _seed_settings_and_usage() -> None:
    for section, values in DEFAULT_SETTINGS.items():
        execute("INSERT OR REPLACE INTO app_settings VALUES(?,?)",
                (section, jdumps(values)))
    usage = [
        ("评论聚类 · 《副业》200 条评论", "demo_llm", "cluster", "demo_fast", 0.08),
        ("原生字幕 · 《职场真相》", "demo_tikhub", "caption", "", 0.10),
        ("拆解 · 《汇报总被打断》", "demo_llm", "analysis", "demo_analysis_model", 0.35),
        ("账号快照同步 ×4", "demo_tikhub", "snapshot", "", 0.0),
    ]
    for task, provider, endpoint, model, cost in usage:
        execute("INSERT INTO usage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid("ul"), "u-zhou", task, provider, endpoint, model, "1 次",
                 cost, 0, 0, ts(0, 9)))


def _seed_jobs() -> None:
    jobs = [
        ("EX-119", "extraction", "FAILED", "transcribe_segments", 40,
         {"title": "大厂裁员潮下的职业选择", "signal": "手动粘贴链接"},
         {"code": "STT_TRANSCRIPTION_FAILED",
          "message": "本地 Whisper 进程异常退出（exit 1），第 3/5 分片失败",
          "stage": "transcribe_segments", "provider": "local_faster_whisper",
          "retryable": True,
          "details": {"segment_index": 3, "completed_segments": 2, "charged": False}},
         0.0),
        ("EX-120", "comment_fetch", "SUCCEEDED", "complete", 100,
         {"title": "评论获取 · 《我劝你别急着做副业》热门 200 条"}, None, 0.08),
        ("CL-9", "comment_cluster", "RUNNING", "clustering", 68,
         {"title": "评论聚类 · 《副业》200 条评论"}, None, 0.08),
        ("EX-122", "extraction", "RUNNING", "transcribe_segments", 60,
         {"title": "工作五年，我劝你学会“偷懒”", "asr": True}, None, 0.0),
        ("EX-121", "extraction", "SUCCEEDED", "complete", 100,
         {"title": "30 岁前必须懂的 3 个职场真相", "caption_hit": True}, None, 0.10),
    ]
    for jid, ttype, status, step, progress, input_, error, cost in jobs:
        started = ts(0, 8) if status != "QUEUED" else None
        finished = ts(0, 9) if status == "SUCCEEDED" else None
        execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, "u-zhou", ttype, status, step, progress, jdumps(input_),
             jdumps({}), jdumps({}), jdumps(error) if error else None,
             new_trace_id(), cost, cost if status == "SUCCEEDED" else 0,
             ts(0, 8), started, finished))
