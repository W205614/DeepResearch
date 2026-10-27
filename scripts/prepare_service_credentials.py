"""Append missing independent credentials to a private env file, without outputting values."""
import argparse
from pathlib import Path
import re
import secrets


KEYS = ("MIGRATOR_DB_PASSWORD", "JAVA_DB_PASSWORD", "PYTHON_DB_PASSWORD", "MONITOR_DB_PASSWORD",
        "OBJECT_STORE_SECRET_KEY", "MILVUS_STORAGE_PASSWORD")


def prepare(path):
    path = Path(path).resolve()
    if not path.is_file() or path.name != ".env":
        raise ValueError("An existing private .env file is required")
    text = path.read_text(encoding="utf-8")
    additions = []
    for key in KEYS:
        match = re.search(rf"(?m)^{key}=(.*)$", text)
        if match and match[1].strip():
            continue
        if match:
            text = text[:match.start()] + text[match.end():]
        additions.append(f"{key}={secrets.token_hex(24)}")
    if additions:
        # Exclusive backup leaves the original recoverable; never overwrites it.
        backup = path.with_name(".env.before-service-roles")
        if not backup.exists():
            with backup.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(path.read_text(encoding="utf-8"))
        path.write_text(text.rstrip() + "\n\n# Independent runtime service credentials\n" + "\n".join(additions) + "\n", encoding="utf-8")
    return len(additions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env")
    args = parser.parse_args()
    print(f"Prepared {prepare(args.env)} missing credentials; values withheld")
