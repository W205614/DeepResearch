"""Bounded pipe transport and kill/reap semantics for untrusted document parsing."""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from ..core.reliability import ServiceError
from .document_parsing import Block

_parser_gate = asyncio.Semaphore(1)

async def parse_document(name, content, settings):
    try:
        async with asyncio.timeout(1):
            await _parser_gate.acquire()
    except TimeoutError:
        raise ServiceError("解析服务正在处理其他文件，请稍后重试", code="parse_busy", retryable=True) from None
    try:
        return await _parse_document(name, content, settings)
    finally:
        _parser_gate.release()


async def _parse_document(name, content, settings):
    if len(content) > 10 * 1024 * 1024:
        raise ServiceError("文件不能超过 10 MB", code="parse_capacity", retryable=False)
    # A child parser does not need any provider, database or object-store secret.
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PYTHONPATH", "LD_LIBRARY_PATH"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    memory_mb = settings.parser_memory_mb
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            limit = int(Path(path).read_text().strip()) // (1024 * 1024)
            memory_mb = min(memory_mb, limit // 2)  # Reserve room for the parent and transport buffers.
        except (OSError, ValueError):
            continue
    if memory_mb < 128:
        raise ServiceError("当前容器解析内存不足，请调整资源后重试", code="parse_capacity", retryable=False)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "backend.services.parser_worker", str(memory_mb),
        str(settings.parser_cpu_seconds), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)

    async def transfer():
        proc.stdin.write(json.dumps({"name": name, "content": base64.b64encode(content).decode()}).encode())
        await proc.stdin.drain()
        proc.stdin.close()
        data, size = [], 0
        while chunk := await proc.stdout.read(65536):
            size += len(chunk)
            if size > settings.parser_output_mb * 1024 * 1024:
                raise ServiceError("解析输出超过安全限制", code="parse_capacity", retryable=False)
            data.append(chunk)
        await proc.wait()
        result = json.loads(b"".join(data))
        if proc.returncode or "error" in result:
            raise ServiceError(result.get("error", "文档解析异常结束"), code="parse_rejected", retryable=False)
        return [Block(locator=row["locator"], text=row["text"],
                      image=base64.b64decode(row["image"], validate=True) if row["image"] else b"",
                      media_type=row["media_type"]) for row in result["blocks"]]
    try:
        async with asyncio.timeout(settings.parser_wall_seconds):
            return await transfer()
    except TimeoutError:
        raise ServiceError("文档解析超时，解析进程已终止，请拆分文件", code="parse_timeout", retryable=False) from None
    except (ValueError, KeyError, BrokenPipeError, ConnectionResetError):
        raise ServiceError("文档解析被资源或格式限制拒绝", code="parse_rejected", retryable=False) from None
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
