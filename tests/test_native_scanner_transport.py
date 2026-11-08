from copy import deepcopy
import hashlib
from io import BytesIO
import json
import tarfile

import pytest

from scripts import check_native_jvm as gate


def fixture():
    archive = BytesIO()
    executable = b"synthetic scanner fixture; never executed"
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        entry = tarfile.TarInfo("syft")
        entry.size = len(executable)
        tar.addfile(entry, BytesIO(executable))
    data = archive.getvalue()
    info = {"url": "https://github.com/anchore/syft/releases/download/v1.55.0/syft_1.55.0_linux_amd64.tar.gz",
            "archive_sha256": hashlib.sha256(data).hexdigest(), "archive_bytes": len(data)}
    metadata = {"tag_name": "v1.55.0", "draft": False, "assets": [{"id": 123,
                "name": "syft_1.55.0_linux_amd64.tar.gz", "digest": "sha256:" + info["archive_sha256"], "size": len(data)}]}
    return data, executable, info, metadata


def test_official_api_preserves_archive_checks_and_never_sends_token(tmp_path, monkeypatch):
    data, binary, info, metadata = fixture()
    calls = []

    def fetch(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            assert request.full_url == "https://api.github.com/repos/anchore/syft/releases/tags/v1.55.0"
            return BytesIO(json.dumps(metadata).encode())
        assert request.full_url == "https://api.github.com/repos/anchore/syft/releases/assets/123"
        assert request.get_header("Accept") == "application/octet-stream"
        return BytesIO(data)

    monkeypatch.setattr(gate, "urlopen", fetch)
    monkeypatch.setattr(gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gate.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-private-token")
    lock = {"tools": {"syft": {"version": "v1.55.0", "linux_amd64": info}}}
    path = gate.executable("syft", lock, tmp_path / "cache", tmp_path / "tools")
    assert path.read_bytes() == binary
    assert gate.executable("syft", lock, tmp_path / "cache", tmp_path / "tools") == path
    assert len(calls) == 2
    assert all(request.get_header("Authorization") is None for request in calls)


@pytest.mark.parametrize("change", ["digest", "size", "name", "id", "duplicate", "draft", "tag"])
def test_changed_release_metadata_is_rejected_before_binary_download(monkeypatch, change):
    _, _, info, metadata = fixture()
    metadata = deepcopy(metadata)
    asset = metadata["assets"][0]
    if change == "digest":
        asset["digest"] = "sha256:" + "0" * 64
    elif change == "size":
        asset["size"] += 1
    elif change == "name":
        asset["name"] = "different-asset"
    elif change == "id":
        asset["id"] = "123"
    elif change == "duplicate":
        metadata["assets"].append(asset.copy())
    elif change == "draft":
        metadata["draft"] = True
    elif change == "tag":
        metadata["tag_name"] = "another-version"
    calls = []

    def fetch(request, timeout):
        calls.append(request)
        assert len(calls) == 1
        return BytesIO(json.dumps(metadata).encode())

    monkeypatch.setattr(gate, "urlopen", fetch)
    with pytest.raises(gate.NativeGateError, match="differs"):
        gate.scanner_archive_response(info)
    assert len(calls) == 1
