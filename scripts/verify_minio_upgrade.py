"""Isolated old-volume S3/IAM/version/multipart/rollback compatibility drill.

Only synthetic fixture volumes and this script's explicitly named containers are
created/stopped. Daily and acceptance deployments are not changed or removed.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import time

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError


def docker(*args):
    result = subprocess.run(["docker", *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Scoped Docker operation failed; private driver output withheld")
    return result.stdout.strip()


def milvus_profile(args, prefix, network, new_server, storage, secret):
    """Real isolated Milvus writes to upgraded S3, then releases/reloads."""
    from pymilvus import MilvusClient

    etcd, milvus = prefix + "-etcd", prefix + "-milvus"
    etcd_volume, milvus_volume = etcd + "-data", milvus + "-data"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    private_env = args.output.parent / (prefix + "-milvus.env")
    private_env.write_text(
        "ETCD_ENDPOINTS=etcd:2379\nMINIO_ADDRESS=" + new_server + ":9000\n"
        "MINIO_REGION=us-east-1\nMINIO_ACCESS_KEY_ID=dr-milvus\n"
        "MINIO_SECRET_ACCESS_KEY=" + secret + "\nGOMAXPROCS=2\nOMP_NUM_THREADS=2\nOPENBLAS_NUM_THREADS=1\n",
        encoding="utf-8",
    )
    for volume in (etcd_volume, milvus_volume):
        docker("volume", "create", volume)
    before = {
        row["Key"] for row in storage.list_objects_v2(Bucket="a-bucket", Prefix="files/").get("Contents", [])
    }
    docker(
        "run",
        "-d",
        "--name",
        etcd,
        "--network",
        network,
        "--network-alias",
        "etcd",
        "--memory",
        "256m",
        "--cpus",
        "1",
        "-v",
        etcd_volume + ":/etcd",
        "quay.io/coreos/etcd:v3.5.25",
        "etcd",
        "-advertise-client-urls=http://etcd:2379",
        "-listen-client-urls",
        "http://0.0.0.0:2379",
        "--data-dir",
        "/etcd",
    )
    docker(
        "run",
        "-d",
        "--name",
        milvus,
        "--network",
        network,
        "--env-file",
        private_env,
        "--memory",
        "1536m",
        "--cpus",
        "1",
        "-p",
        f"127.0.0.1:{args.port + 3}:19530",
        "-v",
        milvus_volume + ":/var/lib/milvus",
        args.milvus_image,
        "milvus",
        "run",
        "standalone",
    )
    client, ready = None, False
    try:
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            try:
                client = MilvusClient(uri=f"http://127.0.0.1:{args.port + 3}", timeout=5)
                client.list_collections()
                ready = True
                break
            except Exception:
                time.sleep(2)
        if not ready:
            raise RuntimeError("Isolated Milvus startup failed")
        collection = "minio_upgrade_fixture"
        client.create_collection(
            collection_name=collection, dimension=8, metric_type="COSINE", consistency_level="Strong"
        )
        client.insert(
            collection_name=collection,
            data=[{"id": 1, "vector": [1.0] + [0.0] * 7}, {"id": 2, "vector": [0.0, 1.0] + [0.0] * 6}],
        )
        client.flush(collection_name=collection, timeout=60)
        after = {
            row["Key"]
            for row in storage.list_objects_v2(Bucket="a-bucket", Prefix="files/").get("Contents", [])
        }
        assert after - before, "Milvus did not persist new S3 binlogs"
        client.release_collection(collection_name=collection)
        client.load_collection(collection_name=collection, timeout=60)
        hits = client.search(collection_name=collection, data=[[1.0] + [0.0] * 7], limit=1)
        assert hits[0][0]["id"] == 1
        return {
            "passed": True,
            "milvus": args.milvus_image,
            "milvus_image": args.milvus_image,
            "rows": 2,
            "new_s3_keys": len(after - before),
            "scope": "real insert/flush S3 binlogs/release/reload/search with scopeddr-milvus IAM",
            "retained_volumes": [etcd_volume, milvus_volume],
            "private_env": str(private_env),
        }
    finally:
        if client:
            client.close()
        docker("stop", milvus, etcd)


def main(args):
    secrets = dict(
        line.split("=", 1)
        for line in args.env.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    root_user, root_password = secrets["MINIO_ROOT_USER"], secrets["MINIO_ROOT_PASSWORD"]
    prefix = "dr-opt-minio-compat-" + args.suffix
    network = prefix + "-net"
    old, new, rollback = (prefix + "-" + name for name in ("old", "new", "rollback"))
    volumes = {name: prefix + "-" + name + "-data" for name in ("old", "new", "rollback")}
    old_id = docker("image", "inspect", args.old_image, "--format", "{{.Id}}")
    candidate_id = docker("image", "inspect", args.candidate, "--format", "{{.Id}}")
    docker("network", "create", network)
    for volume in volumes.values():
        docker("volume", "create", volume)

    def server(name, image, volume, port):
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            name,
            "--env-file",
            args.env,
            "-p",
            f"127.0.0.1:{port}:9000",
            "-v",
            volume + ":/data",
            image,
            "minio",
            "server",
            "/data",
            "--console-address",
            ":9001",
        )

    def client(port, user=root_user, password=root_password):
        return boto3.client(
            "s3",
            endpoint_url=f"http://127.0.0.1:{port}",
            aws_access_key_id=user,
            aws_secret_access_key=password,
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4", retries={"max_attempts": 1}, connect_timeout=3, read_timeout=10
            ),
        )

    def ready(port):
        endpoint = client(port)
        for _ in range(60):
            try:
                endpoint.list_buckets()
                return endpoint
            except Exception:
                time.sleep(1)
        raise RuntimeError("Isolated fixture server did not become ready")

    server(old, old_id, volumes["old"], args.port)
    source = ready(args.port)
    # Existing production bootstrap logic creates distinct scoped object/Milvus
    # accounts in the old server; their IAM state must survive the volume copy.
    docker(
        "run",
        "--name",
        prefix + "-bootstrap",
        "--network",
        network,
        "--env-file",
        args.env,
        "-e",
        "MINIO_BOOTSTRAP_ENDPOINT=" + old + ":9000",
        args.bootstrap_image,
    )
    bucket = "deepresearch-documents"
    source.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    v1 = source.put_object(Bucket=bucket, Key="uploads/versions.txt", Body=b"original version1")["VersionId"]
    v2 = source.put_object(Bucket=bucket, Key="uploads/versions.txt", Body=b"original version2")["VersionId"]
    source.put_object(Bucket=bucket, Key="public/open.txt", Body=b"public fixture")
    source.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "s3:GetObject",
                        "Resource": "arn:aws:s3:::" + bucket + "/public/*",
                    }
                ],
            }
        ),
    )
    milvus = client(args.port, "dr-milvus", secrets["MILVUS_STORAGE_PASSWORD"])
    multipart = milvus.create_multipart_upload(Bucket="a-bucket", Key="files/multipart.bin")["UploadId"]
    part1 = b"a" * (5 * 1024 * 1024)
    etag1 = milvus.upload_part(
        Bucket="a-bucket", Key="files/multipart.bin", UploadId=multipart, PartNumber=1, Body=part1
    )["ETag"]
    source.put_object(Bucket="a-bucket", Key="files/binlog-fixture", Body=b"milvus binlog fixture")
    fixture = {
        "versions": [v1, v2],
        "upload_id": multipart,
        "part1_etag": etag1,
        "part1_sha256": hashlib.sha256(part1).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_name("seed-" + args.suffix + ".json").write_text(
        json.dumps(fixture, indent=2), encoding="utf-8"
    )
    docker("stop", old)
    # Both clones come from the stopped2024 source, never from the upgraded disk.
    for name in ("new", "rollback"):
        docker(
            "run",
            "--name",
            prefix + "-copy-" + name,
            "-v",
            volumes["old"] + ":/source:ro",
            "-v",
            volumes[name] + ":/dest",
            "alpine:3.22",
            "sh",
            "-c",
            "cd /source && tar cf - . | tar xf - -C /dest",
        )
    server(new, candidate_id, volumes["new"], args.port + 1)
    target = ready(args.port + 1)

    def verify_original(endpoint, port):
        assert endpoint.get_bucket_versioning(Bucket=bucket)["Status"] == "Enabled"
        assert (
            endpoint.get_object(Bucket=bucket, Key="uploads/versions.txt", VersionId=v1)["Body"].read()
            == b"original version1"
        )
        assert (
            endpoint.get_object(Bucket=bucket, Key="uploads/versions.txt", VersionId=v2)["Body"].read()
            == b"original version2"
        )
        assert endpoint.get_bucket_policy(Bucket=bucket)["Policy"]
        scoped = client(port, "dr-objects", secrets["OBJECT_STORE_SECRET_KEY"])
        assert (
            scoped.get_object(Bucket=bucket, Key="uploads/versions.txt")["Body"].read()
            == b"original version2"
        )
        for denied_bucket, key in [("a-bucket", "files/binlog-fixture"), (bucket, "public/open.txt")]:
            try:
                scoped.put_object(Bucket=denied_bucket, Key=key, Body=b"forbidden")
                raise AssertionError("Scoped IAM boundary lost")
            except ClientError as error:
                assert error.response["Error"]["Code"] in {"AccessDenied", "Forbidden"}
        anonymous = boto3.client(
            "s3", endpoint_url=f"http://127.0.0.1:{port}", config=Config(signature_version=UNSIGNED)
        )
        assert anonymous.get_object(Bucket=bucket, Key="public/open.txt")["Body"].read() == b"public fixture"
        scoped_milvus = client(port, "dr-milvus", secrets["MILVUS_STORAGE_PASSWORD"])
        assert scoped_milvus.get_bucket_location(Bucket="a-bucket")
        assert (
            scoped_milvus.get_object(Bucket="a-bucket", Key="files/binlog-fixture")["Body"].read()
            == b"milvus binlog fixture"
        )
        assert scoped_milvus.list_objects_v2(Bucket="a-bucket", Prefix="files/")["Contents"]
        parts = scoped_milvus.list_parts(Bucket="a-bucket", Key="files/multipart.bin", UploadId=multipart)[
            "Parts"
        ]
        args.output.with_name("parts-" + str(port) + ".json").write_text(
            json.dumps(parts, default=str, indent=2), encoding="utf-8"
        )
        assert parts[0]["PartNumber"] == 1 and parts[0]["ETag"] == etag1
        return scoped_milvus

    upgraded_milvus = verify_original(target, args.port + 1)
    part2 = b"b" * (1024 * 1024)
    etag2 = upgraded_milvus.upload_part(
        Bucket="a-bucket", Key="files/multipart.bin", UploadId=multipart, PartNumber=2, Body=part2
    )["ETag"]
    upgraded_milvus.complete_multipart_upload(
        Bucket="a-bucket",
        Key="files/multipart.bin",
        UploadId=multipart,
        MultipartUpload={"Parts": [{"ETag": etag1, "PartNumber": 1}, {"ETag": etag2, "PartNumber": 2}]},
    )
    assert (
        upgraded_milvus.get_object(Bucket="a-bucket", Key="files/multipart.bin")["Body"].read()
        == part1 + part2
    )
    upgraded_milvus.delete_object(Bucket="a-bucket", Key="files/binlog-fixture")
    try:
        upgraded_milvus.get_object(Bucket="a-bucket", Key="files/binlog-fixture")
        raise AssertionError("Delete did not apply")
    except ClientError as error:
        assert error.response["Error"]["Code"] == "NoSuchKey"
    # Small compressed long record proves the real S3 Select parser rejects
    # decompression amplification without dropping normal Select functionality.
    for name, body, compression in [
        ("oversized.csv", b"x" * (2 * 1024 * 1024), "NONE"),
        ("oversized.csv.gz", gzip.compress(b"x" * (8 * 1024 * 1024)), "GZIP"),
    ]:
        target.put_object(Bucket=bucket, Key="uploads/" + name, Body=body)
        try:
            response = target.select_object_content(
                Bucket=bucket,
                Key="uploads/" + name,
                ExpressionType="SQL",
                Expression="SELECT * FROM S3Object s",
                InputSerialization={"CSV": {"FileHeaderInfo": "NONE"}, "CompressionType": compression},
                OutputSerialization={"CSV": {}},
            )
            list(response["Payload"])
            raise AssertionError("Oversized Select record was accepted")
        except (ClientError, __import__("botocore").exceptions.EventStreamError) as error:
            assert error.response["Error"]["Code"] == "OverMaxRecordSize"
    target.put_object(Bucket=bucket, Key="uploads/legal.csv", Body=b"42,fixture\n")
    response = target.select_object_content(
        Bucket=bucket,
        Key="uploads/legal.csv",
        ExpressionType="SQL",
        Expression="SELECT * FROM S3Object s",
        InputSerialization={"CSV": {"FileHeaderInfo": "NONE"}},
        OutputSerialization={"CSV": {}},
    )
    records = b"".join(event["Records"]["Payload"] for event in response["Payload"] if "Records" in event)
    assert b"42,fixture" in records
    profile = milvus_profile(args, prefix, network, new, upgraded_milvus, secrets["MILVUS_STORAGE_PASSWORD"])
    server(rollback, old_id, volumes["rollback"], args.port + 2)
    restored = ready(args.port + 2)
    verify_original(restored, args.port + 2)
    result = {
        "passed": True,
        "old_image": old_id,
        "candidate_image": candidate_id,
        "fixture": fixture,
        "checks": [
            "old2024 object versions retained",
            "old IAM identities retained",
            "cross-bucket IAM denial retained",
            "bucket anonymous prefix policy retained",
            "old multipart part retained and completed afterupgrade",
            "Milvus S3 location/list/get/put/delete/multipart interfaces",
            "oversized plain andgzip Select rejected OverMaxRecordSize",
            "legal CSV Select still works",
            "old rollbackcopy reopens2024 original data/IAM/multipart",
        ],
        "source_volumes_untouched": True,
        "retained_fixture_containers": [old, new, rollback, prefix + "-bootstrap"],
        "retained_fixture_volumes": volumes,
        "network": network,
        "supplier_http": 0,
        "real_milvus_profile": profile,
        "scope": "Synthetic objects and dedicated volumes only; old rollbackcopy is pre-upgrade, never an in-place downgrade. S3 API compatibility is not full Milvus workload/production proof.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    docker("stop", new, rollback)
    print(json.dumps({"passed": True, "evidence": str(args.output), "supplier_http": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--old-image", default="dr-opt-acceptance-minio:20261026")
    parser.add_argument("--candidate", default="dr-opt-minio-go1273:20261026")
    parser.add_argument("--bootstrap-image", default="dr-opt-storage-init:20261026")
    parser.add_argument("--milvus-image", default="milvusdb/milvus:v3.0.0")
    parser.add_argument("--suffix", default="20261026")
    parser.add_argument("--port", type=int, default=39080)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
