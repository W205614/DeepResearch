from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_enterprise_compose_persists_safe_logs_and_traces_without_docker_socket():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "grafana/tempo:" in compose
    assert "grafana/loki:" in compose
    assert "/var/run/docker.sock" not in compose

    collector = (ROOT / "observability" / "otel-collector.yaml").read_text(encoding="utf-8")
    assert "otlp/tempo:" in collector
    assert "otlphttp/loki:" in collector
    assert "logs:" in collector

    loki = (ROOT / "observability" / "loki.yaml").read_text(encoding="utf-8")
    assert "allow_structured_metadata: true" in loki
    assert "backend: local" in (ROOT / "observability" / "tempo.yaml").read_text(encoding="utf-8")
