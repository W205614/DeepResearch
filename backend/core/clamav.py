"""ClamAV INSTREAM adapter. The caller owns quarantine persistence."""
import asyncio
import struct

from ..infrastructure.providers import ServiceError


class ScanUnavailable(ServiceError):
    """The scanner cannot make a safety decision; callers must fail closed."""


class ScanRejected(ServiceError):
    """The scanner rejected content; callers retain it only in quarantine."""


async def scan(settings, content: bytes) -> None:
    if settings.document_scan_mode != "clamav":
        return
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port), 5
        )
        writer.write(b"zINSTREAM\0")
        for index in range(0, len(content), 65536):
            part = content[index:index + 65536]
            writer.write(struct.pack("!I", len(part)) + part)
        writer.write(struct.pack("!I", 0))
        await writer.drain()
        result = (await asyncio.wait_for(reader.read(1024), 15)).decode("utf-8", "replace")
        writer.close()
        await writer.wait_closed()
    except Exception as exc:
        raise ScanUnavailable("文件扫描服务不可用，资料保持隔离，尚未进入资料库") from exc
    if "OK" not in result:
        raise ScanRejected("文件安全扫描未通过，资料已隔离，不能用于研究或导出")
