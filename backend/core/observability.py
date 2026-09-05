"""Privacy-safe task logging for the local backend container."""
import logging


LOGGER_NAME = "deepresearch.task"


def configure_task_logger(level: str) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [task] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_task_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def run_label(run_id: str) -> str:
    return str(run_id)[:8]


def error_category(error: Exception) -> str:
    """Return stable diagnostics without logging user content or remote URLs."""
    text = str(error)
    if "超时" in text or "网络" in text or "连接" in text:
        return "network"
    if "内网" in text or "保留地址" in text or "DNS" in text:
        return "source_blocked"
    if "正文" in text or "登录" in text or "付费" in text:
        return "source_unreadable"
    if "API 密钥" in text or "额度" in text:
        return "provider_configuration"
    if "模型" in text or "JSON" in text:
        return "model_response"
    if "时限" in text:
        return "timeout"
    return type(error).__name__.lower()
