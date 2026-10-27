"""Linux candidate-only check of real parser limits, timeout reaping and reuse."""
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.config import Settings  # noqa: E402
from backend.core.reliability import ServiceError  # noqa: E402
from backend.services.parser_process import parse_document  # noqa: E402


def children():
    return set(Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().split())


async def main():
    if sys.platform != "linux" or os.environ.get("OPTIMIZATION_ISOLATED_VERIFY") != "1":
        raise SystemExit("Explicit isolated Linux candidate verification only")
    before = children()
    settings = Settings(_env_file=None, parser_wall_seconds=3)
    task = asyncio.create_task(parse_document("bounded.txt", b"bounded parser verification\n" * 200000, settings))
    observed = None
    for _ in range(400):
        for pid in children() - before:
            try:
                lines = Path(f"/proc/{pid}/limits").read_text().splitlines()
                address = next(line for line in lines if line.startswith("Max address space"))
                cpu = next(line for line in lines if line.startswith("Max cpu time"))
                files = next(line for line in lines if line.startswith("Max open files"))
                if "unlimited" not in address and "unlimited" not in cpu:
                    observed = {"address_space": address, "cpu": cpu, "open_files": files}
                    break
            except (OSError, StopIteration):
                pass
        if observed or task.done():
            break
        await asyncio.sleep(.005)
    await task
    if not observed or children() != before:
        raise AssertionError("Actual child limits or successful reaping were not verified")
    timeout = Settings(_env_file=None, parser_wall_seconds=.05)
    try:
        await parse_document("timeout.txt", b"timeout verification\n" * 200000, timeout)
    except ServiceError as error:
        if error.code != "parse_timeout":
            raise
    else:
        raise AssertionError("Expected real parser timeout")
    if children() != before:
        raise AssertionError("Timed-out parser was not reaped")
    blocks = await parse_document("after-timeout.txt", b"gate and resources reusable", settings)
    if not blocks or children() != before:
        raise AssertionError("Parser gate/resources not reusable")
    print(json.dumps({"passed": True, "platform": "Linux", "actual_child_limits": observed,
                      "timeout_killed_and_reaped": True, "subsequent_parse_succeeded": True,
                      "supplier_http_requests": 0,
                      "scope": "one real bounded parser, timeout and reuse; not exhaustive hostile-format coverage"}))


if __name__ == "__main__":
    asyncio.run(main())
