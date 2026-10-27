"""No network: reject unbound downloads and verify credential isolation."""

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from urllib.error import HTTPError, URLError

import pytest

from scripts import fetch_approved_image as fetch
from scripts import runtime_ci_inventory as inventory


class Response(BytesIO):
    def __init__(self, data, declared=None):
        super().__init__(data)
        self.headers = {} if declared is None else {"Content-Length": str(declared)}


@pytest.fixture(autouse=True)
def transport_only_publication_stub(monkeypatch):
    # Live release checks have dedicated positive/cache-bypass tests in test_artifact_publication.
    monkeypatch.setattr(fetch, "verify_release_publication", lambda *args, **kwargs: {"published_verified_live": True})


def download_fixture():
    data = b"fixed-OCI-fixture-not-an-actual-archive"
    return (
        data,
        {"archive_bytes": len(data), "archive_sha256": hashlib.sha256(data).hexdigest()},
        {
            "repository": fetch.REPOSITORY,
            "asset_id": 678114320,
            "asset_name": "approved-minio-151cd83d.oci.tar",
            "asset_sha256": hashlib.sha256(data).hexdigest(),
            "api_url": "https://api.github.com/repos/" + fetch.REPOSITORY + "/releases/assets/678114320",
        },
    )


def test_redirect_drops_token_and_only_writes_complete_bound_bytes(tmp_path, monkeypatch):
    data, binding, transport = download_fixture()
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(
                    request.full_url,
                    302,
                    "redirect",
                    {"Location": "https://release-assets.githubusercontent.com/fixed?signed=fixture"},
                    None,
                )
            return Response(data, len(data))

    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-secret-never-forward")
    monkeypatch.setattr(fetch, "build_opener", lambda handler: Opener())
    target = tmp_path / "approved.tar"
    assert fetch.download_archive(target, binding, transport) == target
    assert target.read_bytes() == data
    assert calls[0].get_header("Authorization") == "Bearer synthetic-secret-never-forward"
    assert calls[1].get_header("Authorization") is None
    assert "api.github.com/repos/" in calls[0].full_url


@pytest.mark.parametrize(
    "redirect",
    [
        "http://release-assets.githubusercontent.com/object",
        "https://release-assets.githubusercontent.com.attacker.invalid/object",
        "https://attacker.invalid/object",
        "https://user:password@release-assets.githubusercontent.com/object",
        "https://objects.githubusercontent.com:8443/object",
        "file:///tmp/artifact",
    ],
)
def test_untrusted_redirect_never_receives_token_or_second_request(tmp_path, monkeypatch, redirect):
    _, binding, transport = download_fixture()
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise HTTPError(request.full_url, 302, "redirect", {"Location": redirect}, None)

    monkeypatch.setattr(fetch, "build_opener", lambda handler: Opener())
    with pytest.raises(fetch.DispositionError, match="Unrecognized"):
        fetch.download_archive(tmp_path / "approved.tar", binding, transport)
    assert len(calls) == 1
    assert not (tmp_path / "approved.tar").exists()


@pytest.mark.parametrize("mode", ["truncated", "oversize", "different", "bad-length"])
def test_incomplete_or_changed_archive_is_never_promoted(tmp_path, monkeypatch, mode):
    data, binding, transport = download_fixture()
    content = {
        "truncated": data[:-1],
        "oversize": data + b"x",
        "different": b"x" * len(data),
        "bad-length": data,
    }[mode]

    class Opener:
        def open(self, request, timeout):
            return Response(content, len(data) + 1 if mode == "bad-length" else None)

    monkeypatch.setattr(fetch, "build_opener", lambda handler: Opener())
    with pytest.raises(fetch.DispositionError):
        fetch.download_archive(tmp_path / "approved.tar", binding, transport)
    assert not (tmp_path / "approved.tar").exists()


def test_wrong_repository_or_existing_archive_is_not_replaced(tmp_path, monkeypatch):
    _, binding, transport = download_fixture()
    monkeypatch.setattr(fetch, "build_opener", lambda *args: pytest.fail("must not send request"))
    wrong = dict(transport, repository="attacker/project")
    with pytest.raises(fetch.DispositionError):
        fetch.download_archive(tmp_path / "approved.tar", binding, wrong)
    target = tmp_path / "approved.tar"
    target.write_bytes(b"keep-for-investigation")
    with pytest.raises(fetch.DispositionError, match="Existing archive differs"):
        fetch.download_archive(target, binding, transport)
    assert target.read_bytes() == b"keep-for-investigation"


def test_signed_url_and_token_never_appear_in_main_failure_output(tmp_path, monkeypatch, capsys):
    _, binding, transport = download_fixture()
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"transport": transport}), encoding="utf-8")
    monkeypatch.setattr(fetch, "ROOT", tmp_path)
    monkeypatch.setattr(fetch, "POLICY", policy)
    monkeypatch.setattr(fetch, "load_binding", lambda: binding)
    monkeypatch.setattr(
        fetch,
        "download_archive",
        lambda *args: (_ for _ in ()).throw(
            URLError("https://objects.githubusercontent.com/?signed=SECRET;Bearer TOKEN")
        ),
    )
    output = tmp_path / "result.json"
    monkeypatch.setattr("sys.argv", ["fetch", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        fetch.main()
    assert error.value.code == 1
    text = capsys.readouterr().out + output.read_text(encoding="utf-8")
    assert "SECRET" not in text and "TOKEN" not in text and "objects.githubusercontent" not in text
    assert json.loads(output.read_text(encoding="utf-8"))["mode"] == "failed_closed"


def test_classic_engine_config_id_requires_exact_diffids_arch_and_os():
    binding = {
        "index_digest": "sha256:index",
        "platform_manifest_digest": "sha256:manifest",
        "config_digest": "sha256:config",
        "diff_ids": ["sha256:layer"],
    }
    actual = {
        "Id": "sha256:config",
        "RootFS": {"Layers": ["sha256:layer"]},
        "Os": "linux",
        "Architecture": "amd64",
    }
    assert fetch.exact_image(actual, binding)
    for key, value in [
        ("Id", "sha256:other"),
        ("RootFS", {"Layers": ["sha256:changed"]}),
        ("Os", "windows"),
        ("Architecture", "arm64"),
    ]:
        changed = dict(actual, **{key: value})
        assert not fetch.exact_image(changed, binding)


def test_minio_load_failure_cannot_retag_or_rebuild(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "inspect", lambda reference: None)

    class Result:
        returncode = 1

    def run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(fetch.subprocess, "run", run)
    with pytest.raises(fetch.DispositionError, match="could not load"):
        fetch.load_image(
            tmp_path / "approved.tar",
            {"index_digest": "sha256:index", "config_digest": "sha256:config"},
            fetch.SCAN_TAG,
        )
    assert len(calls) == 1 and calls[0][1] == "load"


def test_inventory_fixed_artifact_cannot_spread_to_other_service_or_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "load_binding", lambda root: {})
    config = {
        "services": {
            "minio": {"image": inventory.APPROVED_MINIO},
            "redis": {"image": "redis:7.4.11-alpine"},
            "app": {"build": {"context": str(tmp_path), "dockerfile": "Dockerfile"}},
        }
    }
    vendor, custom = inventory.matrices(config, tmp_path)
    assert [(v["services"], v["approved_artifact"]) for v in vendor["include"]] == [
        ("minio", True),
        ("redis", False),
    ]
    assert custom["include"][0]["services"] == "app"
    for mutate in (
        lambda c: c["services"]["minio"].update(build={"context": str(tmp_path)}),
        lambda c: c["services"]["minio"].update(image="rebuilt-minio:new"),
        lambda c: c["services"].update(other={"image": inventory.APPROVED_MINIO}),
    ):
        altered = deepcopy(config)
        mutate(altered)
        with pytest.raises(ValueError):
            inventory.matrices(altered, tmp_path)


@pytest.mark.parametrize("suffix", ["a" * 63, "a" * 65, "g" * 64, "a" * 64 + "extra"])
def test_inventory_rejects_malformed_digest_before_ci_matrix(tmp_path, monkeypatch, suffix):
    monkeypatch.setattr(inventory, "load_binding", lambda root: {})
    config = {
        "services": {
            "minio": {"image": inventory.APPROVED_MINIO},
            "clamav": {"image": "clamav/clamav:1.4.6_base@sha256:" + suffix},
        }
    }
    with pytest.raises(ValueError, match="complete SHA256"):
        inventory.matrices(config, tmp_path)


def test_grafana_matrix_has_independent_artifact_namespace_and_no_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "load_binding", lambda root: {})
    monkeypatch.setattr(inventory, "load_grafana_binding", lambda root: {})
    config = {
        "services": {
            "minio": {"image": inventory.APPROVED_MINIO},
            "grafana": {"image": inventory.APPROVED_GRAFANA},
            "other": {"image": "alpine:3.24.3"},
        }
    }
    matrix, _ = inventory.matrices(config, tmp_path)
    approved = {r["artifact"]: r for r in matrix["include"] if r["approved_artifact"]}
    assert set(approved) == {"minio", "grafana"}
    for update in ({"build": {"context": str(tmp_path)}}, {"image": "unreviewed-grafana:new"}):
        changed = deepcopy(config)
        changed["services"]["grafana"].update(update)
        with pytest.raises(ValueError):
            inventory.matrices(changed, tmp_path)
    changed = deepcopy(config)
    changed["services"]["other"]["image"] = inventory.APPROVED_GRAFANA
    with pytest.raises(ValueError, match="namespaces"):
        inventory.matrices(changed, tmp_path)
