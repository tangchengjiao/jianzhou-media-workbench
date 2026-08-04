"""Typed Error 体系：显式失败，不伪造成功。

任何外部服务/模型/业务错误都以 AppError 抛出，API 层统一转换为
{success: false, error: {...}} 结构，携带 Trace ID。
"""
import datetime
import logging
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def new_trace_id() -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d")
    return f"tr_{ts}_{uuid.uuid4().hex[:8]}"


class AppError(Exception):
    def __init__(self, code: str, message: str, stage: str = "",
                 retryable: bool = False, provider: str | None = None,
                 details: dict | None = None, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable
        self.provider = provider
        self.details = details or {}
        self.http_status = http_status
        self.trace_id = new_trace_id()

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "provider": self.provider,
            "retryable": self.retryable,
            "trace_id": self.trace_id,
            "details": self.details,
        }


# ---- 常用错误工厂 ----
def config_missing_api_key(provider: str) -> AppError:
    return AppError(
        "CONFIG_MISSING_API_KEY",
        f"未配置 {provider} 的 API Key。请在系统设置 → 数据来源/大模型 中填写后重试；"
        "演示模式下可使用内置演示数据。",
        stage="config_check", retryable=False, provider=provider,
    )


def tikhub_unauthorized(account: str) -> AppError:
    return AppError(
        "TIKHUB_UNAUTHORIZED",
        f"TikHub API Key 已失效或被吊销（HTTP 401），账号 {account} 同步失败。"
        "自动重试已停止，未生成任何替代数据。",
        stage="account_sync", retryable=False, provider="tikhub",
        details={"http_status": 401, "charged": False,
                 "fix": "① 更新 API Key → ② 手动重试同步"},
    )


def llm_unavailable(model: str) -> AppError:
    return AppError(
        "LLM_PROVIDER_UNAVAILABLE",
        f"模型 {model} 当前不可用（连接超时 10s）。系统不会自动切换到未授权模型，"
        "请在系统设置中手动调整路由后重试。",
        stage="llm_call", retryable=True, provider="anthropic",
        details={"charged": False},
    )


def stt_failed(segment: int, total: int) -> AppError:
    return AppError(
        "STT_TRANSCRIPTION_FAILED",
        f"第 {segment}/{total} 个音频分片转写失败：本地 Whisper 进程异常退出（exit 1）。"
        f"已完成 {segment - 1} 个分片并保留为部分产物，未标记为完整转写。",
        stage="transcribe_segments", retryable=True, provider="local_faster_whisper",
        details={"segment_index": segment, "completed_segments": segment - 1,
                 "charged": False},
    )


def not_found(what: str, rid: str) -> AppError:
    return AppError("NOT_FOUND", f"{what}不存在：{rid}", stage="lookup",
                    http_status=404)


def state_invalid(msg: str) -> AppError:
    return AppError("CONTENT_STATE_INVALID", msg, stage="state_machine")


def prediction_locked() -> AppError:
    return AppError(
        "PREDICTION_ALREADY_LOCKED",
        "发布前判断已锁定，只读不可修改。仅可创建新版本或在发布后追加复盘。",
        stage="prediction_lock",
    )


def validation_failed(msg: str) -> AppError:
    return AppError("VALIDATION_FAILED", msg, stage="schema_validation")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        _log_error(exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={"success": False, "error": exc.to_dict()},
        )

    @app.exception_handler(Exception)
    async def unknown_error_handler(request: Request, exc: Exception):
        # 顶层捕获只用于可观测性边界：写日志、生成 Trace、不伪造业务成功
        err = AppError("UNEXPECTED_ERROR", "服务处理请求时发生未预期错误，请使用 Trace ID 查询本地日志。",
                       stage="unknown", http_status=500)
        logger.exception("Unhandled request error trace_id=%s path=%s",
                         err.trace_id, request.url.path)
        _log_error(err)
        return JSONResponse(status_code=500,
                            content={"success": False, "error": err.to_dict()})


def _log_error(exc: AppError) -> None:
    try:
        from .db import execute
        execute(
            "INSERT INTO error_logs(trace_id, code, stage, provider, message, ts)"
            " VALUES(?,?,?,?,?,?)",
            (exc.trace_id, exc.code, exc.stage, exc.provider or "",
             exc.message, datetime.datetime.now().isoformat(timespec="seconds")),
        )
    except Exception:
        pass  # 日志失败不阻断错误返回
