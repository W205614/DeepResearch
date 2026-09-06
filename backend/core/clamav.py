"""ClamAV INSTREAM client; no user data is retained by this adapter."""
import asyncio
import struct
from ..infrastructure.providers import ServiceError

async def scan(settings, content: bytes) -> None:
    if settings.document_scan_mode != "clamav":
        return
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(settings.clamav_host, settings.clamav_port), 5)
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
        raise ServiceError("文件扫描服务不可用，资料未被接收") from exc
    if "OK" not in result:
        raise ServiceError("文件安全扫描未通过，资料已隔离")