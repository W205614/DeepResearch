# Tempo dependency rebuild

The official Tempo 2.10.10, 3.0.5 and 3.1.1 source tags still require
`github.com/go-jose/go-jose/v4 v4.1.4`. Upgrading to another currently published
stable Tempo tag does not fix CVE-2026-116587.

`deployment/docker/Dockerfile.tempo` keeps the 2.10.10 source tag, runtime root,
user and entrypoint. It rebuilds `/tempo` with the full checked-in Go locks,
go-jose 4.1.6 and x/net 0.62.0 plus its required Go submodule versions. The source
tag object and commit are checked before compilation, and Git verifies the
complete fetched object graph.
Compiler build information must contain the fixed dependency versions.

The Compose storage and OTLP configuration stay on the 2.10.10 interfaces.
This source change supplies no vulnerability waiver; the resulting image must
pass the ordinary runtime image scan and component compatibility checks.

The fixed module locks resolve with Go 1.27.3 in readonly mode. Local registry
and GitHub transport failed with TLS errors, so an actual image build and scan
have not completed locally and remain requirements for remote CI.
