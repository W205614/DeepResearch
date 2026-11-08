import subprocess
import zipfile

import pytest

from scripts import local, prepare_service_credentials, verify_source_release


def initialize(tmp_path):
    (tmp_path / ".env.example").write_bytes((local.ROOT / ".env.example").read_bytes())
    path = tmp_path / ".env"
    local.initialize(tmp_path, path)
    return path


def test_initialization_generates_distinct_credentials_and_preserves_existing_file(tmp_path, capsys):
    path = initialize(tmp_path)
    before = path.read_bytes()
    values = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    credentials = [values[key] for key in prepare_service_credentials.KEYS]
    assert len(set(credentials)) == 11
    assert all(len(value) == 48 for value in credentials)
    assert not local.initialize(tmp_path, path)
    assert path.read_bytes() == before
    assert not any(value in capsys.readouterr().out for value in credentials)


@pytest.mark.parametrize("empty", ["", '""', "''"])
def test_prepare_retains_original_configuration_and_handles_quoted_empty_passwords(tmp_path, empty):
    path = tmp_path / ".env"
    original = f"POSTGRES_PASSWORD=keep-this-existing-password\nJAVA_DB_PASSWORD={empty}\nLLM_API_KEY=keep-provider-key\n"
    path.write_text(original)
    assert prepare_service_credentials.prepare(path) == 10
    assert (tmp_path / ".env.before-service-roles").read_text() == original
    assert "POSTGRES_PASSWORD=keep-this-existing-password\n" in path.read_text()
    assert "LLM_API_KEY=keep-provider-key\n" in path.read_text()
    before = path.read_bytes()
    assert prepare_service_credentials.prepare(path) == 0
    assert path.read_bytes() == before


def test_configuration_requires_providers_and_allows_explicit_reranker_opt_out(tmp_path):
    path = initialize(tmp_path)
    with pytest.raises(ValueError, match="LLM_API_KEY, EMBEDDING_API_KEY, RERANKER_API_KEY"):
        local.check_configuration(path)
    text = path.read_text().replace("LLM_API_KEY=\n", "LLM_API_KEY=synthetic-only\n")
    text = text.replace("EMBEDDING_API_KEY=\n", "EMBEDDING_API_KEY=synthetic-only\n")
    path.write_text(text.replace("RERANKER_ENABLED=true", "RERANKER_ENABLED=false"))
    local.check_configuration(path)


def test_stop_preserves_selected_project_and_overrides_without_requiring_provider_keys(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(local.subprocess, "run", lambda command, **kwargs: calls.append(command))
    override = tmp_path / "ports.yaml"
    assert local.main(["stop", "--project-name", "isolated-fixture", "--env-file", str(tmp_path / ".env"),
                       "--compose-file", str(override)]) == 0
    assert len(calls) == 1
    command = calls[0]
    assert command[-3:] == ["stop", "--timeout", "60"]
    assert command[command.index("-p") + 1] == "isolated-fixture"
    assert command[-4] == str(override.resolve())
    assert "down" not in command


def source_repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    for name in ["LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", ".env.example", "scripts/local.py"]:
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("synthetic source fixture\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def commit(root):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "Synthetic source fixture"], cwd=root, check=True)


def test_source_archive_uses_committed_source_and_excludes_ignored_configuration(tmp_path):
    root = source_repository(tmp_path)
    (root / ".gitignore").write_text(".env\n.cache/\n")
    (root / ".env").write_text("SYNTHETIC_PRIVATE_VALUE=must-not-be-exported\n")
    (root / ".cache").mkdir()
    (root / ".cache/private.txt").write_text("synthetic local output")
    commit(root)
    output = tmp_path / "output"
    report = verify_source_release.verify(root, "HEAD", output)
    assert report["source_only"]
    assert len(report["source_sha"]) == 40
    with zipfile.ZipFile(output / "source.zip") as archive:
        assert ".env" not in archive.namelist()
        assert ".env.example" in archive.namelist()
        assert not any(name.startswith(".cache/") for name in archive.namelist())


@pytest.mark.parametrize("private_path", [".env", "nested/.env.production", ".cache/private.json", ".local/private.txt", "node_modules/dependency.js"])
def test_source_archive_rejects_accidentally_tracked_private_paths(tmp_path, private_path):
    root = source_repository(tmp_path)
    path = root / private_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic private data")
    commit(root)
    with pytest.raises(ValueError, match="Private path"):
        verify_source_release.verify(root, "HEAD", tmp_path / "output")


def test_source_archive_rejects_symbolic_links(tmp_path):
    root = source_repository(tmp_path)
    commit(root)
    blob = subprocess.check_output(["git", "hash-object", "-w", "--stdin"], input=b"../outside", cwd=root).decode().strip()
    subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"120000,{blob},outside-link"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "Synthetic symbolic link"], cwd=root, check=True)
    with pytest.raises(ValueError, match="symbolic link"):
        verify_source_release.verify(root, "HEAD", tmp_path / "output")
