"""One document per disposable process; no model, network or storage credentials used."""
import base64
import json
import os
import sys


def limits(memory_mb, cpu_seconds):
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    elif os.name == "nt":
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ro", "wo", "oo", "rb", "wb", "ob")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x100 | 0x8 | 0x2
        info.basic.active = 1
        info.basic.process_time = cpu_seconds * 10_000_000
        info.process_memory = memory_mb * 1024 * 1024
        if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("parser resource boundary unavailable")
        if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
            raise OSError("parser resource boundary unavailable")
        # The job must stay alive until process exit.
        globals()["_job_handle"] = handle
    else:
        raise OSError("unsupported parser isolation platform")


def main():
    limits(int(sys.argv[1]), int(sys.argv[2]))
    from .document_parsing import extract_blocks
    raw = sys.stdin.buffer.read(15 * 1024 * 1024 + 1)
    if len(raw) > 15 * 1024 * 1024:
        raise ValueError("input capacity")
    payload = json.loads(raw)
    content = base64.b64decode(payload["content"], validate=True)
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("input capacity")
    blocks = extract_blocks(payload["name"], content)
    output, total = [], 0
    for block in blocks:
        total += len(block.text.encode("utf-8")) + len(block.image)
        if total > 24 * 1024 * 1024 or len(output) >= 2000:
            raise ValueError("output capacity")
        output.append({"locator": block.locator, "text": block.text,
                       "image": base64.b64encode(block.image).decode() if block.image else "",
                       "media_type": block.media_type})
    sys.stdout.write(json.dumps({"blocks": output}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No document text, paths, environment or upstream errors on stderr.
        sys.stdout.write(json.dumps({"error": str(exc)[:200] if exc.__class__.__name__ in
                                   {"InvalidDocument", "ServiceError"} else "文档解析被资源或格式限制拒绝"}))
        sys.exit(2)
