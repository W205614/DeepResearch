"""Check four deliberate plugin integrity failures in an owned isolated fixture.

The supplied container must use only synthetic credentials and the tamper image;
it is stopped on exit. No live volume or external network is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from verify_grafana_upgrade import _TARGETS, api, docker, wait_ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new evidence filename")
    info = json.loads(docker("inspect", args.container))[0]
    assert info["Config"]["Labels"].get("io.deepresearch.validation") == "optimization-20261026"
    assert info["HostConfig"]["NetworkMode"] == "none"
    assert not info.get("Mounts"), "No live or other mounted data is accepted"
    assert "tamper" in info["Config"]["Image"]
    assert not info["State"]["Running"], "Fixture must start stopped"
    env = dict(x.split("=", 1) for x in info["Config"]["Env"] if "=" in x)
    credentials = (env["GF_SECURITY_ADMIN_USER"], env["GF_SECURITY_ADMIN_PASSWORD"])
    _TARGETS["tamper"] = args.container
    cases = {
        "loki": ("modified", "reviewed backend ELF changed"),
        "mysql": ("modified", "signed frontend module changed"),
        "jaeger": ("modified", "unknown extra path added"),
        "deepresearch-unsigned-fixture": ("unsigned", "unsigned new plugin added"),
    }
    receipt = {"status": "failed", "scope": "synthetic isolated integrity fixture only",
               "image_config": info["Image"], "container": args.container,
               "security_pass": False, "cases": [], "external_contacts": 0}
    try:
        docker("start", args.container)
        receipt["version"] = wait_ready("tamper")
        code, plugins = api("tamper", "api/plugins", credentials)
        assert code == 200
        rows = {x["id"]: x for x in plugins}
        for plugin_id, (signature, mutation) in cases.items():
            assert plugin_id not in rows or rows[plugin_id]["signature"] != "valid"
            code, _ = api("tamper", "api/plugins/" + plugin_id + "/settings", credentials)
            # Upstream GetPluginSettingByID checks the error resolver before the
            # plugin store, returning 500 for a recorded signature rejection.
            # Require that precise validation log below; generic HTTP500 is not proof.
            assert code == 500, (plugin_id, code)
            logs = docker("logs", args.container)
            pattern = (r'logger=plugins\.signature\.validation[^\n]*'
                       r'pluginId=' + re.escape(plugin_id) + r' status=' + signature)
            match = re.search(pattern, logs)
            assert match, (plugin_id, "missing signature rejection evidence")
            assert re.search(r'path=/api/plugins/' + re.escape(plugin_id)
                             + r'/settings[^\n]*error="plugin', logs)
            receipt["cases"].append({"plugin": plugin_id, "mutation": mutation,
                                      "settings_http": code, "signature": signature,
                                      "validation_log": match.group(0)})
        code, good = api("tamper", "api/plugins/prometheus/settings", credentials)
        assert code == 200 and good["signature"] == "valid"
        receipt["untouched_official_plugin_valid"] = "prometheus"
        receipt["log_sha256"] = hashlib.sha256(docker("logs", args.container).encode()).hexdigest()
        receipt["status"] = "passed"
    finally:
        docker("stop", "-t", "20", args.container)
        receipt["fixture_stopped"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "cases": len(receipt["cases"]),
                      "fixture_stopped": True}))


if __name__ == "__main__":
    main()
