"""Non-model queue pressure probe. Run only against a disposable enterprise demo."""
import asyncio
from backend.core.config import Settings
from backend.services.runtime import Runtime

async def main():
    runtime = await Runtime(Settings()).start()
    try:
        before = await runtime.db.one("SELECT COUNT(*) AS total FROM runs WHERE status='queued'")
        print({"queued_before": int(before["total"]), "note": "Use k6 or authenticated API traffic for full HTTP load; this probe only confirms queue metric wiring."})
        await runtime.refresh_queue_depth()
    finally:
        await runtime.close()
if __name__ == "__main__":
    asyncio.run(main())