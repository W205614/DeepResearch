from copy import deepcopy
import hashlib
from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from scripts import fetch_approved_image as fetch
from tests.test_grafana_parts_transport import fixture as parts_fixture


def live_release(monkeypatch, *, artifact="grafana", change=None, status=None):
    spec = fetch.artifact_spec(artifact)
    record = {"id": spec["release_id"], "tag_name": spec["release_tag"], "draft": False,
              "published_at": "2026-10-26T20:00:00Z"}
    if change:
        record.update(change)
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if status:
                raise HTTPError(request.full_url, status, "private-response", {}, None)
            return BytesIO(json.dumps(record).encode())

    monkeypatch.setattr(fetch, "build_opener", lambda handler: Opener())
    monkeypatch.setattr(fetch, "open_asset_response", lambda *args: pytest.fail("cached rejection must not request asset"))
    return calls


@pytest.mark.parametrize("case", ["draft", "missing_date", "wrong_id", "wrong_tag", "bad_date", "404"])
def test_remote_parts_cache_cannot_bypass_live_published_release(tmp_path, monkeypatch, case):
    data, binding, transport, _ = parts_fixture(monkeypatch)
    changes = {"draft": {"draft": True}, "missing_date": {"published_at": None},
               "wrong_id": {"id": 1}, "wrong_tag": {"tag_name": "other"}, "bad_date": {"published_at": "yesterday"}}
    live_release(monkeypatch, change=changes.get(case), status=404 if case == "404" else None)
    target = tmp_path / "cached.tar"
    target.write_bytes(data)
    with pytest.raises(fetch.DispositionError):
        fetch.download_archive(target, binding, transport, artifact="grafana")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == binding["archive_sha256"]


def test_single_minio_cache_also_requires_live_publication_and_token_stays_on_fixed_api(tmp_path, monkeypatch):
    spec = fetch.artifact_spec("minio")
    data = b"approved-cache"
    binding = {"archive_bytes": len(data), "archive_sha256": hashlib.sha256(data).hexdigest()}
    transport = {"repository": fetch.REPOSITORY, "release_id": spec["release_id"], "tag": spec["release_tag"],
                 "asset_id": spec["asset_id"], "asset_name": spec["asset_name"], "asset_sha256": binding["archive_sha256"],
                 "api_url": f"https://api.github.com/repos/{fetch.REPOSITORY}/releases/assets/{spec['asset_id']}"}
    target = tmp_path / "cached.tar"
    target.write_bytes(data)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    calls = live_release(monkeypatch, artifact="minio", change={"draft": True})
    with pytest.raises(fetch.DispositionError, match="not published"):
        fetch.download_archive(target, binding, transport)
    assert calls[0].full_url == "https://api.github.com/repos/W205614/DeepResearch/releases/424228449"
    assert calls[0].get_header("Authorization") == "Bearer synthetic-token"


def test_explicit_unpublished_mode_is_distinct_and_published_cache_is_checked_each_time(tmp_path, monkeypatch):
    data, binding, transport, _ = parts_fixture(monkeypatch)
    target = tmp_path / "cached.tar"
    target.write_bytes(data)
    live_release(monkeypatch, change={"draft": True, "published_at": None})
    receipt = {}
    fetch.download_archive(target, binding, transport, artifact="grafana", allow_unpublished_verification=True,
                           publication_receipt=receipt)
    assert receipt["published_verified_live"] is False and receipt["unpublished_verification_only"] is True
    calls = live_release(monkeypatch)
    receipt = {}
    fetch.download_archive(target, binding, transport, artifact="grafana", publication_receipt=receipt)
    assert receipt["published_verified_live"] is True and receipt["unpublished_verification_only"] is False
    assert len(calls) == 1
    changed = deepcopy(transport)
    changed["release_id"] = 1
    with pytest.raises(fetch.DispositionError, match="scope"):
        fetch.download_archive(target, binding, changed, artifact="grafana")
