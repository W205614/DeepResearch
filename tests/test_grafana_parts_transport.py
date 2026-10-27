from copy import deepcopy
import hashlib
from io import BytesIO

import pytest

from scripts import fetch_approved_image as fetch
from scripts.image_disposition import DispositionError


@pytest.fixture(autouse=True)
def transport_only_publication_stub(monkeypatch):
    # This module tests content/assembly; publication checks have their own focused regressions.
    monkeypatch.setattr(fetch, "verify_release_publication", lambda *args, **kwargs: {"published_verified_live": True})


class Response(BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def fixture(monkeypatch):
    monkeypatch.setattr(fetch, "PART_BYTES", 5)
    data = b"exact-archive"
    binding = {"archive_bytes": len(data), "archive_sha256": hashlib.sha256(data).hexdigest()}
    name = "grafana-9a68d3a7.oci.tar"
    parts = []
    chunks = {}
    for i, start in enumerate(range(0, len(data), 5)):
        chunk = data[start : start + 5]
        asset = 700000000 + i
        sha = hashlib.sha256(chunk).hexdigest()
        parts.append(
            {
                "index": i,
                "bytes": len(chunk),
                "sha256": sha,
                "asset_name": name + f".part-{i:03d}",
                "asset_id": asset,
                "api_url": f"https://api.github.com/repos/{fetch.REPOSITORY}/releases/assets/{asset}",
                "remote_digest": "sha256:" + sha,
                "remote_state": "uploaded",
            }
        )
        chunks[asset] = chunk
    transport = {
        "artifact": "grafana",
        "transport_mode": "github_asset_parts",
        "repository": fetch.REPOSITORY,
        "release_id": 424356978,
        "release_tag": "artifact-grafana-reviewed-20261026",
        "asset_name": name,
        "asset_sha256": binding["archive_sha256"],
        "archive_bytes": len(data),
        "part_bytes": 5,
        "parts": parts,
        "asset_id": None,
        "api_url": None,
        "whole_archive_server_digest": None,
    }
    return data, binding, transport, chunks


def test_interruption_reuses_only_verified_parts_and_assembles_exact_whole(tmp_path, monkeypatch):
    data, binding, transport, chunks = fixture(monkeypatch)
    calls = []
    fail = True

    def response(asset, hosts):
        calls.append(asset)
        chunk = chunks[asset]
        if fail and asset == transport["parts"][1]["asset_id"]:
            return Response(chunk[:-1])
        return Response(chunk)

    monkeypatch.setattr(fetch, "open_asset_response", response)
    target = tmp_path / "approved-grafana.oci.tar"
    with pytest.raises(DispositionError):
        fetch.download_archive(target, binding, transport, artifact="grafana")
    assert not target.exists()
    assert len(list(tmp_path.rglob("*.part"))) == 1
    calls.clear()
    fail = False
    assert fetch.download_archive(target, binding, transport, artifact="grafana").read_bytes() == data
    assert calls == [p["asset_id"] for p in transport["parts"][1:]]
    assert hashlib.sha256(target.read_bytes()).hexdigest() == binding["archive_sha256"]


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "order",
        "name",
        "server_digest",
        "starter",
        "api_repo",
        "duplicate_id",
        "size",
        "fake_whole_digest",
        "wrong_repo",
    ],
)
def test_incomplete_or_mixed_parts_fail_before_network_even_with_cached_whole(tmp_path, monkeypatch, case):
    data, binding, transport, _ = fixture(monkeypatch)
    target = tmp_path / "approved-grafana.oci.tar"
    target.write_bytes(data)
    monkeypatch.setattr(
        fetch, "open_asset_response", lambda *args: pytest.fail("must validate complete manifest first")
    )
    if case == "missing":
        transport["parts"].pop()
    elif case == "order":
        transport["parts"].reverse()
    elif case == "name":
        transport["parts"][0]["asset_name"] = "approved-minio.oci.tar.part-000"
    elif case == "server_digest":
        transport["parts"][0]["remote_digest"] = None
    elif case == "starter":
        transport["parts"][0]["remote_state"] = "starter"
    elif case == "api_repo":
        transport["parts"][0]["api_url"] = "https://api.github.com/repos/attacker/repo/releases/assets/1"
    elif case == "duplicate_id":
        transport["parts"][1]["asset_id"] = transport["parts"][0]["asset_id"]
    elif case == "size":
        transport["parts"][0]["bytes"] = 6
    elif case == "fake_whole_digest":
        transport["whole_archive_server_digest"] = "sha256:" + binding["archive_sha256"]
    elif case == "wrong_repo":
        transport["repository"] = "attacker/repo"
    with pytest.raises(DispositionError):
        fetch.download_archive(target, binding, transport, artifact="grafana")
    assert target.read_bytes() == data


def test_corrupt_part_or_whole_sha_is_never_promoted(tmp_path, monkeypatch):
    _, binding, transport, chunks = fixture(monkeypatch)
    monkeypatch.setattr(
        fetch, "open_asset_response", lambda asset, hosts: Response(b"x" * len(chunks[asset]))
    )
    with pytest.raises(DispositionError):
        fetch.download_archive(tmp_path / "first.tar", binding, transport, artifact="grafana")
    assert not (tmp_path / "first.tar").exists()
    monkeypatch.setattr(fetch, "open_asset_response", lambda asset, hosts: Response(chunks[asset]))
    changed = deepcopy(binding)
    changed["archive_sha256"] = "f" * 64
    transport["asset_sha256"] = changed["archive_sha256"]
    with pytest.raises(DispositionError, match="whole SHA"):
        fetch.download_archive(tmp_path / "second.tar", changed, transport, artifact="grafana")
    assert not (tmp_path / "second.tar").exists()
    assert list(tmp_path.rglob("*.partial"))


def test_parts_mode_is_not_a_minio_transport_or_unbounded_part_count(tmp_path, monkeypatch):
    _, binding, transport, _ = fixture(monkeypatch)
    with pytest.raises(DispositionError, match="mode"):
        fetch.download_archive(tmp_path / "minio.tar", binding, transport, artifact="minio")
    changed = deepcopy(binding)
    changed["archive_bytes"] = 5 * 33
    transport["archive_bytes"] = changed["archive_bytes"]
    transport["parts"] *= 11
    with pytest.raises(DispositionError, match="inventory"):
        fetch.validate_parts(changed, transport)
