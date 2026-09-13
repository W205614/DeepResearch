"""Logical recovery drill against the literal dr-verify fixture, never production."""
import json
from pathlib import Path
import subprocess
import time

COMPOSE = ["docker", "compose", "-f", "compose.verify.yaml", "-p", "dr-verify"]
TARGET = "reliability_restore_check"
TABLES = ("runs", "memberships", "documents", "chunks", "verified_claims")


def run(args, data=None):
    result = subprocess.run(COMPOSE + args, input=data, capture_output=True)
    if result.returncode:
        raise RuntimeError("Isolated recovery command failed; output withheld")
    return result.stdout


def sql(database, statement):
    return run(["exec", "-T", "postgres", "psql", "-U", "deepresearch", "-d", database,
                "-v", "ON_ERROR_STOP=1", "-Atc", statement]).decode().strip()


OBJECT_PROBE = '''
import boto3, hashlib, json
from backend.core.config import Settings
s=Settings()
c=boto3.client('s3',endpoint_url=s.object_store_endpoint,aws_access_key_id=s.object_store_access_key.get_secret_value(),aws_secret_access_key=s.object_store_secret_key.get_secret_value())
target='reliability-restore-check'
assert target not in [b['Name'] for b in c.list_buckets()['Buckets']], 'Restore bucket already exists'
c.create_bucket(Bucket=target)
copied=[]
try:
    for page in c.get_paginator('list_objects_v2').paginate(Bucket=s.object_store_bucket):
        for obj in page.get('Contents',[]):
            key=obj['Key']
            body=c.get_object(Bucket=s.object_store_bucket,Key=key)['Body'].read()
            c.put_object(Bucket=target,Key=key,Body=body)
            copied.append(key)
            restored=c.get_object(Bucket=target,Key=key)['Body'].read()
            assert hashlib.sha256(body).digest()==hashlib.sha256(restored).digest()
    assert copied, 'No source objects; restore was not exercised'
    print(json.dumps({'restored_objects_checked':len(copied)}))
finally:
    for key in copied:c.delete_object(Bucket=target,Key=key)
    c.delete_bucket(Bucket=target)
'''


def main():
    started = time.monotonic()
    if sql("deepresearch", "SELECT count(*) FROM runs WHERE status IN ('running','queued')") != "0":
        raise RuntimeError("Wait for fixture tasks to finish")
    counts = {table: sql("deepresearch", f"SELECT count(*) FROM {table}") for table in TABLES}
    if counts["runs"] == "0":
        raise RuntimeError("Seed fixture research before restoring")
    archive = run(["exec", "-T", "postgres", "pg_dump", "-U", "deepresearch", "-d", "deepresearch", "-Fc"])
    # CREATE DATABASE fails if target exists; never adopt or overwrite that target.
    sql("postgres", f"CREATE DATABASE {TARGET}")
    try:
        run(["exec", "-T", "postgres", "pg_restore", "-U", "deepresearch", "-d", TARGET, "--exit-on-error"], archive)
        for table, expected in counts.items():
            assert sql(TARGET, f"SELECT count(*) FROM {table}") == expected
        assert sql(TARGET, "SELECT version_num FROM alembic_version") == "0008_reliability"
        restored = json.loads(run(["exec", "-T", "api", "python", "-c", OBJECT_PROBE]).decode().strip().splitlines()[-1])
        result = {"passed": True, "scope": "isolated logical SQL and object restore; not full host disaster recovery",
                  "tables_checked": len(TABLES), **restored, "seconds": round(time.monotonic()-started, 2)}
        Path(".cache/reliability-logical-recovery.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        sql("postgres", f"DROP DATABASE {TARGET}")


if __name__ == "__main__":
    main()
