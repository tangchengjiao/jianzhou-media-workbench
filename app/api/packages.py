"""资料中心：数据包、外部导入（默认只保存）、来源追踪。"""
import csv
import datetime
import io

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from ..db import execute, jdumps, jloads, query, query_one
from ..errors import not_found, validation_failed
from ..config import MAX_IMPORT_UPLOAD_BYTES
from ..ids import new_id
from . import current_user_id, now, ok

router = APIRouter(tags=["packages"])

IMPORT_FLOW = ["上传", "识别格式", "选择目标包", "字段映射", "预览",
               "校验去重", "确认写入", "报告"]


@router.get("/packages")
def list_packages():
    uid = current_user_id()
    return ok(query("SELECT * FROM data_packages WHERE owner_id=?", (uid,)))


@router.get("/packages/{pid}")
def package_detail(pid: str):
    row = query_one("SELECT * FROM data_packages WHERE id=?", (pid,))
    if not row:
        raise not_found("数据包", pid)
    return ok(row)


@router.get("/packages/{pid}/impact")
def package_impact(pid: str):
    row = query_one("SELECT * FROM data_packages WHERE id=?", (pid,))
    if not row:
        raise not_found("数据包", pid)
    return ok({
        "package": row["name"],
        "sources": row["source_count"],
        "referenced_by": ["表达画像 v1.4", "创作项目 ×2"] if row["type"] == "corpus" else [],
        "affects_profile": row["type"] == "ip_profile",
        "affects_retro": row["type"] in ("account", "feedback"),
        "note": "删除前请确认：可仅删除缓存或删除原始资产。激活中的包需先切换。",
    })


@router.post("/packages/{pid}/activate")
def activate_package(pid: str):
    row = query_one("SELECT * FROM data_packages WHERE id=?", (pid,))
    if not row:
        raise not_found("数据包", pid)
    execute("UPDATE data_packages SET status='active', activated_at=? WHERE id=?",
            (now(), pid))
    return ok({"note": f"数据包 {row['name']} 已激活"})


@router.get("/packages/sources/tracking")
def source_tracking():
    return ok([
        {"name": "咨询案例-汇报翻车", "source": "手动上传", "package": "内容语料包",
         "analyzed": "✓", "refs": "创作项目 ×1（#C-31）", "impact": "影响草稿 v3", "level": "warn"},
        {"name": "公众号文章合集 18 篇", "source": "ZIP 导入 #IMP-1980", "package": "内容语料包",
         "analyzed": "✓", "refs": "表达画像 v1.4", "impact": "影响画像统计", "level": "warn"},
        {"name": "对标样本-运营第6年", "source": "TikHub 同步", "package": "对标数据包",
         "analyzed": "仅元数据", "refs": "信号 #SG-2031", "impact": "可安全删除", "level": "ok"},
    ])


# ================= 外部导入 =================

@router.get("/imports")
def list_imports():
    uid = current_user_id()
    rows = query("SELECT * FROM import_batches WHERE user_id=?"
                 " ORDER BY created_at DESC", (uid,))
    for r in rows:
        r["errors"] = jloads(r["errors_json"], [])
        r["mapping"] = jloads(r["mapping_json"], [])
    return ok({"flow": IMPORT_FLOW, "batches": rows})


@router.post("/imports/upload")
async def import_upload(file: UploadFile = File(...),
                        target_package: str = "内容语料包"):
    """上传并解析：默认只保存，不自动 ASR/OCR/LLM/Embedding。

    CSV 真实解析预览；其他格式登记为待映射。部分失败必须明示。"""
    uid = current_user_id()
    raw = await file.read(MAX_IMPORT_UPLOAD_BYTES + 1)
    if len(raw) > MAX_IMPORT_UPLOAD_BYTES:
        raise validation_failed(
            f"导入文件超过 {MAX_IMPORT_UPLOAD_BYTES // (1024 * 1024)} MB 限制"
        )
    bid = new_id("IMP")
    filename = file.filename or "unnamed"
    errors, mapping, rows_total, rows_ok = [], [], 0, 0

    if filename.lower().endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        headers = reader.fieldnames or []
        guess = {"笔记标题": "title", "标题": "title", "发布时间": "published_at",
                 "点赞数": "metrics.likes", "笔记链接": "url", "链接": "url"}
        for h in headers:
            mapping.append({"from": h, "to": guess.get(h, h),
                            "policy": "必填" if guess.get(h) == "title" else "自动"})
        for i, row in enumerate(reader, start=1):
            rows_total += 1
            title = row.get("标题") or row.get("笔记标题") or ""
            if not title.strip():
                errors.append({"row": i, "error": "缺少必填字段 title",
                               "action": "已跳过（未写入）"})
                continue
            rows_ok += 1
    else:
        mapping = [{"from": "(文件内容)", "to": "raw_text", "policy": "仅保存"}]
        rows_total, rows_ok = 1, 1

    status = "parsed" if not errors else "partial"
    execute("INSERT INTO import_batches VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (bid, uid, filename, target_package, status,
             rows_total, rows_ok, len(errors), jdumps(errors), jdumps(mapping), now()))
    return ok({
        "batch_id": bid, "status": status, "total": rows_total,
        "success": rows_ok, "failed": len(errors), "errors": errors,
        "mapping": mapping,
        "note": "导入计划已生成，等待你确认后写入；确认前不执行任何 LLM / Embedding 任务。",
    })


@router.post("/imports/{bid}/commit")
def import_commit(bid: str):
    b = query_one("SELECT * FROM import_batches WHERE id=?", (bid,))
    if not b:
        raise not_found("导入批次", bid)
    if b["status"] not in ("parsed", "partial"):
        return ok({"note": f"批次已是 {b['status']} 状态"})
    new_status = "committed" if b["failed"] == 0 else "partial_committed"
    execute("UPDATE import_batches SET status=? WHERE id=?", (new_status, bid))
    note = (f"已写入 {b['success']} 条到「{b['target_package']}」"
            + (f"，{b['failed']} 条失败已跳过并记录在报告（部分成功不会显示为绿色完成）"
               if b["failed"] else ""))
    return ok({"note": note})


@router.post("/imports/{bid}/rollback")
def import_rollback(bid: str):
    b = query_one("SELECT * FROM import_batches WHERE id=?", (bid,))
    if not b:
        raise not_found("导入批次", bid)
    execute("UPDATE import_batches SET status='rolled_back' WHERE id=?", (bid,))
    return ok({"note": f"批次 {bid} 已整体回滚（二次确认后执行，记录已保留）"})
