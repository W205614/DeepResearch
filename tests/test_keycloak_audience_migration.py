from copy import deepcopy
import json

import httpx
import pytest

from scripts.reconcile_keycloak_audience import AUDIENCE, CLIENT_ID, MAPPER_NAME, reconcile


class Realm:
    def __init__(self):
        self.mapper = {"id": "mapper-id", "name": MAPPER_NAME, "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper", "consentRequired": False,
            "config": {"included.client.audience": AUDIENCE, "access.token.claim": "true", "id.token.claim": "false"}}
        self.writes = []
        self.race = False
        self.resource_client = False

    def request(self, request):
        path = request.url.path
        if path.endswith("/clients"):
            if self.resource_client and request.url.params.get("clientId") == AUDIENCE:
                return httpx.Response(200, json=[{"id": "api", "clientId": AUDIENCE, "enabled": False}])
            return httpx.Response(200, json=[{"id": "web-id", "clientId": CLIENT_ID}, {"id": "other", "clientId": "other"}])
        if path.endswith("/models"):
            result = deepcopy(self.mapper)
            if self.race:
                self.mapper["config"]["id.token.claim"] = "true"
            return httpx.Response(200, json=[result])
        assert path.endswith("/clients/web-id/protocol-mappers/models/mapper-id")
        if request.method == "PUT":
            self.mapper = json.loads(request.content)
            self.writes.append(deepcopy(self.mapper))
            return httpx.Response(204)
        return httpx.Response(200, json=deepcopy(self.mapper))


def test_exact_mapper_apply_idempotence_and_rollback(tmp_path):
    realm = Realm()
    original = deepcopy(realm.mapper)
    backup = tmp_path / "before.private.json"
    with httpx.Client(transport=httpx.MockTransport(realm.request)) as client:
        assert reconcile(client, "http://localhost:8180", "deepresearch")["change_required"]
        assert not realm.writes and not backup.exists()
        assert reconcile(client, "http://localhost:8180", "deepresearch", backup=backup, apply=True)["status"] == "applied"
        assert realm.mapper["config"] == {"included.custom.audience": AUDIENCE, "access.token.claim": "true", "id.token.claim": "false"}
        assert json.loads(backup.read_text())["before"] == original
        assert reconcile(client, "http://localhost:8180", "deepresearch", backup=backup, apply=True)["status"] == "already_desired"
        assert len(realm.writes) == 1
        assert reconcile(client, "http://localhost:8180", "deepresearch", rollback=backup, apply=True)["status"] == "rolled_back"
        assert realm.mapper == original
        assert reconcile(client, "http://localhost:8180", "deepresearch", rollback=backup, apply=True)["status"] == "already_rolled_back"
        assert len(realm.writes) == 2


def test_unexpected_audience_never_mutates(tmp_path):
    realm = Realm()
    realm.mapper["config"]["included.client.audience"] = "different-api"
    with httpx.Client(transport=httpx.MockTransport(realm.request)) as client:
        with pytest.raises(ValueError, match="Unexpected audience"):
            reconcile(client, "http://localhost", "deepresearch", backup=tmp_path / "before.json", apply=True)
    assert not realm.writes


def test_real_disabled_resource_client_is_not_bypassed(tmp_path):
    realm = Realm()
    realm.resource_client = True
    backup = tmp_path / "before.json"
    with httpx.Client(transport=httpx.MockTransport(realm.request)) as client:
        with pytest.raises(ValueError, match="resource client exists"):
            reconcile(client, "http://localhost", "deepresearch", backup=backup, apply=True)
    assert not realm.writes and not backup.exists()


def test_concurrent_configuration_change_is_not_overwritten(tmp_path):
    realm = Realm()
    realm.race = True
    with httpx.Client(transport=httpx.MockTransport(realm.request)) as client:
        with pytest.raises(ValueError, match="changed during preparation"):
            reconcile(client, "http://localhost", "deepresearch", backup=tmp_path / "before.json", apply=True)
    assert not realm.writes


def test_rollback_refuses_different_realm_and_post_migration_changes(tmp_path):
    realm = Realm()
    backup = tmp_path / "before.json"
    with httpx.Client(transport=httpx.MockTransport(realm.request)) as client:
        reconcile(client, "http://localhost", "deepresearch", backup=backup, apply=True)
        with pytest.raises(ValueError, match="does not belong"):
            reconcile(client, "http://localhost", "another-realm", rollback=backup, apply=True)
        realm.mapper["config"]["id.token.claim"] = "true"
        with pytest.raises(ValueError, match="changed after migration"):
            reconcile(client, "http://localhost", "deepresearch", rollback=backup, apply=True)
    assert len(realm.writes) == 1


def test_backup_is_not_overwritten_and_admin_http_is_explicit(tmp_path):
    realm = Realm()
    backup = tmp_path / "before.json"
    backup.write_text("earlier backup")
    with httpx.Client(transport=httpx.MockTransport(realm.request)) as client:
        with pytest.raises(ValueError, match="Administrative HTTP"):
            reconcile(client, "http://external.example", "deepresearch")
        with pytest.raises(FileExistsError):
            reconcile(client, "http://localhost", "deepresearch", backup=backup, apply=True)
    assert backup.read_text() == "earlier backup" and not realm.writes
