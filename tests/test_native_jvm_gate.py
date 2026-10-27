from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import base64
import json
import io
import tarfile

import pytest

from scripts.check_native_jvm import NativeGateError, validate_database, validate_jvm, validate_scan, verify_jre_tar, validate_full_inventory, validate_source


def jvm_fixture():
    expected = {"java_version":"21.0.13","semantic_version":"21.0.13+9","implementor":"Eclipse Adoptium","source_repo":"https://github.com/adoptium/jdk21u.git","cpe":"cpe:2.3:a:oracle:openjdk:21.0.13:*:*:*:*:*:*:*","version":"21.0.13+9-LTS"}
    profile = {"release_path":"/opt/java/openjdk/release","libc":"musl"}
    artifact = {"name":"openjdk","type":"binary","metadataType":"java-jvm-installation","foundBy":"java-jvm-cataloger","version":expected["version"],"metadata":{"release":{"javaVersion":"21.0.13","semanticVersion":"21.0.13+9","implementor":"Eclipse Adoptium","sourceRepo":expected["source_repo"],"libc":"musl","osArch":"x86_64","osName":"Linux","imageType":"JRE"}},"locations":[{"path":profile["release_path"]}],"cpes":[{"cpe":expected["cpe"],"source":"declared"}]}
    return {"artifacts":[artifact]}, profile, expected


def test_actual_jvm_requires_one_runtime_and_exact_version_source_abi_and_cpe():
    sbom, profile, expected = jvm_fixture()
    assert validate_jvm(sbom,profile,expected)["version"]=="21.0.13+9-LTS"
    mutants=[]
    for field,value in [("javaVersion","21.0.12.1"),("sourceRepo","https://attacker.invalid"),("libc","gnu")]:
        changed=deepcopy(sbom)
        changed["artifacts"][0]["metadata"]["release"][field]=value
        mutants.append(changed)
    changed=deepcopy(sbom)
    changed["artifacts"][0]["cpes"]=[]
    mutants.extend([changed,{"artifacts":[]},{"artifacts":sbom["artifacts"]*2}])
    for changed in mutants:
        with pytest.raises(NativeGateError):
            validate_jvm(changed,profile,expected)


def tar_bytes(files):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode="w") as tar:
        for name,data in files.items():
            member=tarfile.TarInfo(name)
            member.size=len(data)
            tar.addfile(member,io.BytesIO(data))
    stream.seek(0)
    return stream


def test_a_forged_release_version_cannot_hide_old_or_extra_binary_bytes():
    files={"release":b"JAVA_VERSION=21.0.13", "bin/java":b"actual-fixed-ELF"}
    expected={name:{"type":"file","sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)} for name,data in files.items()}
    assert verify_jre_tar(tar_bytes(files),expected)["verified_entries"]==2
    for changed in [dict(files,**{"bin/java":b"old-vulnerable-ELF"}),dict(files,**{"lib/old.so":b"extra"}),dict(files,**{"../escape":b"unsafe"})]:
        with pytest.raises(NativeGateError):
            verify_jre_tar(tar_bytes(changed),expected)


def test_pinned_database_rejects_stale_missing_and_changed_bytes(tmp_path):
    path=tmp_path/"db"
    path.write_bytes(b"fixed-advisory-db")
    now=datetime(2026,10,26,tzinfo=timezone.utc)
    spec={"built":now.isoformat(),"max_snapshot_age_days":7,"db_bytes":path.stat().st_size,"db_sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    validate_database(path,spec,now=now)
    with pytest.raises(NativeGateError):
        validate_database(path,spec,now=now+timedelta(days=8))
    path.write_bytes(b"tampered")
    with pytest.raises(NativeGateError):
        validate_database(path,spec,now=now)
    with pytest.raises(NativeGateError):
        validate_database(tmp_path/"missing",spec,now=now)


def test_native_and_full_scan_reject_os_high_unknown_ignored_or_missing_report():
    sbom={"source":{"metadata":{"imageID":"sha256:" + "a" * 64}}}
    good={"source":{"target":{"imageID":"sha256:" + "a" * 64}},"descriptor":{"name":"grype","version":"0.121.0","db":{"status":{"valid":True}}},"matches":[{"vulnerability":{"severity":"Medium"}}]}
    assert validate_scan(good,sbom)["Medium"]==1
    for changed in [{},dict(good,ignoredMatches=[{"id":"ignored"}]),dict(good,source={"target":{"imageID":"sha256:other"}})]+[dict(good,matches=[{"artifact":{"type":"apk","name":"openssl"},"vulnerability":{"severity":s}}]) for s in ["High","Critical","Unknown","unexpected",None]]:
        with pytest.raises(NativeGateError):
            validate_scan(changed,sbom)


def test_full_inventory_cannot_drop_os_packages_or_unknown_versions():
    sbom={"artifacts":[{"name":"openjdk","version":"21.0.13+9-LTS","type":"binary"},{"name":"openssl","version":"3.5.9","type":"apk"}]}
    profile={"minimum_packages":{"binary":1,"apk":1}}
    validate_full_inventory(sbom,profile)
    for altered in [{"artifacts":sbom["artifacts"][:1]}, {"artifacts":[dict(sbom["artifacts"][0],version="unknown"),sbom["artifacts"][1]]}]:
        with pytest.raises(NativeGateError):
            validate_full_inventory(altered,profile)


def test_oci_index_and_config_identity_bind_through_real_manifest_and_diffids():
    config={"architecture":"amd64","os":"linux","config":{"User":"app"},"rootfs":{"diff_ids":["sha256:layer"]}}
    raw=json.dumps(config).encode()
    config_id="sha256:"+hashlib.sha256(raw).hexdigest()
    manifest=json.dumps({"config":{"digest":config_id}}).encode()
    inspection={"Id":"sha256:"+"b"*64,"Architecture":"amd64","Os":"linux","RootFS":{"Layers":["sha256:layer"]},"Config":{"User":"app"}}
    sbom={"source":{"type":"image","metadata":{"userInput":inspection["Id"],"imageID":config_id,"manifestDigest":"sha256:"+hashlib.sha256(manifest).hexdigest(),"config":base64.b64encode(raw).decode(),"manifest":base64.b64encode(manifest).decode()}}}
    validate_source(sbom,inspection)
    for key,value in [("imageID","sha256:"+"c"*64),("userInput","sha256:"+"d"*64),("config",""),("manifestDigest","sha256:"+"e"*64)]:
        changed=deepcopy(sbom)
        changed["source"]["metadata"][key]=value
        with pytest.raises(NativeGateError):
            validate_source(changed,inspection)
    with pytest.raises(NativeGateError):
        validate_source(sbom,dict(inspection,RootFS={"Layers":["sha256:changed"]}))
