"""Reconcile one fixed audience mapper; existing realms are not re-imported.

Default is read-only. Supply a short-lived realm-management token through
KEYCLOAK_ADMIN_TOKEN, never as a command-line argument. Apply/rollback belong in
the operator's backed-up upgrade window; this script does not restart services.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

CLIENT_ID = "deepresearch-web"
MAPPER_NAME = "deepresearch-api-audience"
AUDIENCE = "deepresearch-api"
PROVIDER = "oidc-audience-mapper"


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def checked_base(value: str, *, allow_http: bool = False) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an explicit Keycloak base URL without credentials/query/fragment")
    loopback = parsed.hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme == "http" and not (loopback or allow_http):
        raise ValueError("Administrative HTTP requires explicit --allow-http for an isolated network")
    return value.rstrip("/")


def validate_mapper(mapper: dict) -> None:
    if mapper.get("name") != MAPPER_NAME or mapper.get("protocolMapper") != PROVIDER or mapper.get("protocol") != "openid-connect":
        raise ValueError("Unexpected mapper identity; no changes made")
    config = mapper.get("config", {})
    if config.get("access.token.claim") != "true":
        raise ValueError("The fixed API audience must already be enabled for access tokens")
    client, custom = config.get("included.client.audience"), config.get("included.custom.audience")
    if client not in (None, AUDIENCE) or custom not in (None, AUDIENCE) or (client is None and custom is None):
        raise ValueError("Unexpected audience configuration; no changes made")


def reconcile(client: httpx.Client, base_url: str, realm: str, *, backup: Path | None = None,
              apply: bool = False, rollback: Path | None = None, allow_http: bool = False) -> dict:
    base = checked_base(base_url, allow_http=allow_http)
    admin = base + "/admin/realms/" + quote(realm, safe="")

    def get(url: str, **kwargs):
        response = client.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    clients = [row for row in get(admin + "/clients", params={"clientId": CLIENT_ID}) if row.get("clientId") == CLIENT_ID]
    if len(clients) != 1:
        raise ValueError("Expected exactly one fixed web client; no changes made")
    client_id = clients[0]["id"]
    path = admin + "/clients/" + quote(client_id, safe="") + "/protocol-mappers/models"
    matches = [row for row in get(path) if row.get("name") == MAPPER_NAME]
    if len(matches) != 1:
        raise ValueError("Expected exactly one fixed audience mapper; no changes made")
    current = matches[0]
    validate_mapper(current)
    if current["config"].get("included.client.audience") == AUDIENCE or rollback:
        resources = [row for row in get(admin + "/clients", params={"clientId": AUDIENCE})
                     if row.get("clientId") == AUDIENCE]
        if resources:
            raise ValueError("An API resource client exists; do not override its enabled/disabled audience semantics")
    endpoint = path + "/" + quote(current["id"], safe="")
    desired = deepcopy(current)
    desired["config"].pop("included.client.audience", None)
    desired["config"]["included.custom.audience"] = AUDIENCE
    result = {"base_url": base, "realm": realm, "client": CLIENT_ID, "mapper": MAPPER_NAME,
              "audience": AUDIENCE, "before_sha256": fingerprint(current), "status": "check_only",
              "change_required": current != desired}
    if rollback:
        saved = json.loads(rollback.read_text(encoding="utf-8"))
        expected = {"base_url": base, "realm": realm, "client_id": client_id, "client": CLIENT_ID,
                    "mapper_id": current["id"], "mapper": MAPPER_NAME, "audience": AUDIENCE}
        if any(saved.get(key) != value for key, value in expected.items()):
            raise ValueError("Backup does not belong to this exact realm/client/mapper")
        before = saved["before"]
        validate_mapper(before)
        if before.get("id") != current["id"] or fingerprint(before) != saved["before_sha256"]:
            raise ValueError("Backup mapper integrity mismatch")
        if current == before:
            return {**result, "status": "already_rolled_back", "change_required": False}
        if fingerprint(current) != saved["after_sha256"]:
            raise ValueError("Mapper changed after migration; refusing to overwrite it")
        desired = before
        result["change_required"] = current != desired
    elif current == desired:
        return {**result, "status": "already_desired", "change_required": False}
    if not apply:
        return result
    if not rollback:
        if backup is None:
            raise ValueError("Apply requires an explicit private backup path")
        saved = {"schema": "keycloak-audience-migration-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                 "base_url": base, "realm": realm, "client_id": client_id, "client": CLIENT_ID,
                 "mapper_id": current["id"], "mapper": MAPPER_NAME, "audience": AUDIENCE,
                 "before": current, "before_sha256": fingerprint(current), "after_sha256": fingerprint(desired)}
        backup.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents overwriting an earlier rollback artifact.
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(saved, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    if fingerprint(get(endpoint)) != fingerprint(current):
        raise ValueError("Mapper changed during preparation; no write performed")
    response = client.put(endpoint, json=desired)
    response.raise_for_status()
    observed = get(endpoint)
    if fingerprint(observed) != fingerprint(desired):
        raise ValueError("Mapper read-back differs; inspect the private backup before any retry")
    return {**result, "status": "rolled_back" if rollback else "applied", "after_sha256": fingerprint(observed),
            "backup": str(rollback or backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--realm", default="deepresearch")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--allow-http", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("KEYCLOAK_ADMIN_TOKEN", "").strip()
    if not token:
        parser.error("Set a short-lived realm-management KEYCLOAK_ADMIN_TOKEN in the environment")
    with httpx.Client(headers={"Authorization": "Bearer " + token}, timeout=15, follow_redirects=False) as client:
        result = reconcile(client, args.base_url, args.realm, backup=args.backup, apply=args.apply,
                           rollback=args.rollback, allow_http=args.allow_http)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
