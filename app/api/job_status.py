"""Unified durable-job API used by every frontend workflow."""
from fastapi import APIRouter

from ..jobs import get_job, list_jobs, request_cancel, retry_job
from ..repositories import owned_row
from . import current_user_id, ok

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def jobs():
    return ok(list_jobs(current_user_id()))


@router.get("/{job_id}")
def job_detail(job_id: str):
    owned_row("jobs", job_id, current_user_id(), label="任务")
    return ok(get_job(job_id))


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    owned_row("jobs", job_id, current_user_id(), label="任务")
    request_cancel(job_id)
    return ok({"note": "已请求取消；当前步骤结束后会停止，已经沉淀的产物继续保留。"})


@router.post("/{job_id}/retry")
def retry_failed_job(job_id: str):
    owned_row("jobs", job_id, current_user_id(), label="任务")
    retry_job(job_id)
    return ok({"note": "任务已从失败点重新排队。"})
