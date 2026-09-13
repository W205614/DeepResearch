"""Private staged images, task ownership, and durable cleanup."""
import asyncio
import io
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from ..core.clamav import scan
from ..core.db import now, uid
from ..infrastructure.providers import ServiceError
from .documents import validate_upload, IMAGE_SUFFIXES, IMAGE_MEDIA_TYPES

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def validate_image(name, content):
    if len(content) > MAX_IMAGE_BYTES:
        raise ServiceError("图片超过 10 MB 限制")
    suffix = validate_upload(name, content)
    if suffix not in IMAGE_SUFFIXES:
        raise ServiceError("附件仅支持 JPEG、PNG、WebP 和静态 GIF 图片")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > 40_000_000:
                    raise ServiceError("图片像素超过 4000 万限制，请缩小后上传")
                if getattr(image, "n_frames", 1) != 1:
                    raise ServiceError("暂不支持动画图片，请上传静态图片")
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("图片损坏或无法解码，请重新导出后上传") from None
    return IMAGE_MEDIA_TYPES[suffix]


class Attachments:
    def __init__(self, db, settings, store):
        self.db, self.settings, self.store = db, settings, store

    async def add(self, workspace, owner, name, content):
        media = await asyncio.to_thread(validate_image, name, content)
        image_id = uid()
        await self.db.execute("""INSERT INTO attachments
            (id,user_id,owner_subject,name,media_type,size,status,created_at)
            VALUES(?,?,?,?,?,?,'scanning',?)""",
            (image_id, workspace, owner, Path(name).name[:200], media, len(content), now()))
        try:
            await self.store.put("quarantine", image_id, content)
            await scan(self.settings, content)
            await self.store.move("quarantine", "uploads", image_id)
            await self.db.execute("UPDATE attachments SET status='ready' WHERE id=?", (image_id,))
        except BaseException:
            await self.db.execute("UPDATE attachments SET status='quarantined' WHERE id=?", (image_id,))
            raise
        return await self.visible(image_id, workspace, owner)

    async def visible(self, image_id, workspace, owner):
        row = await self.db.one("SELECT * FROM attachments WHERE id=? AND user_id=? AND status='ready'",
                                (image_id, workspace))
        if not row:
            raise LookupError("图片不存在或不可访问")
        if row["run_id"]:
            if not await self.db.one("SELECT id FROM runs WHERE id=? AND user_id=?", (row["run_id"], workspace)):
                raise LookupError("图片不存在或不可访问")
        elif row["owner_subject"] != owner:
            raise LookupError("图片不存在或不可访问")
        return row

    async def staged(self, ids, workspace, owner):
        for image_id in ids:
            row = await self.visible(image_id, workspace, owner)
            if row["run_id"] or row["owner_subject"] != owner:
                raise ServiceError("图片已绑定其他任务，请重新上传")

    async def for_run(self, run_id):
        return await self.db.rows("SELECT id,name,media_type,size,position FROM attachments WHERE run_id=? AND status='ready' ORDER BY position", (run_id,))

    async def remove(self, image_id, workspace, owner):
        async with self.db.guard("workspace:" + workspace):
            row = await self.visible(image_id, workspace, owner)
            if row["run_id"]:
                raise ServiceError("已发送的图片随会话保存，请通过删除会话清理")
            await self.db.execute("UPDATE attachments SET status='deleted' WHERE id=?", (image_id,))
        await self.cleanup()

    async def delete_runs(self, ids):
        for run_id in ids:
            await self.db.execute("UPDATE attachments SET status='deleted' WHERE run_id=?", (run_id,))
            await self.db.execute("DELETE FROM run_vision WHERE run_id=?", (run_id,))

    async def cleanup(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        workspaces = await self.db.rows("SELECT DISTINCT user_id FROM attachments WHERE run_id='' AND created_at<?", (cutoff,))
        for item in workspaces:
            async with self.db.guard("workspace:" + item["user_id"]):
                await self.db.execute("UPDATE attachments SET status='deleted' WHERE user_id=? AND run_id='' AND created_at<?", (item["user_id"], cutoff))
        for row in await self.db.rows("SELECT id FROM attachments WHERE status='deleted'"):
            await self.store.delete("uploads", row["id"])
            await self.store.delete("quarantine", row["id"])
            await self.db.execute("DELETE FROM attachments WHERE id=? AND status='deleted'", (row["id"],))
