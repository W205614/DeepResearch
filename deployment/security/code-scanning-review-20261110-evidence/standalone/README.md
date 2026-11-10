# Four independent source contexts

`review.json` supplements the initial Trivy register for alerts **14, 22, 23, 48**. Each complete `go list -deps -test -json -mod=readonly` probe exited zero for Linux/amd64, CGO disabled, without extra optional build tags. The etcd tools probe enumerated all 15 actual command imports from its `tools.go`; it did not treat an empty tools directory as coverage.

The first attempts failed on missing checksums and remain locally preserved. Only the isolated copies' `go.sum` files were completed using the official Sum/GoModSum of already selected versions. Repository source, original locks, replacement directives and dependency versions were unchanged. This proves the stated package applicability; it does **not** claim those original snapshots build without checksum completion, that all optional build tags are safe, or that a whole old MongoDB/x/crypto module has been fixed.

The OpenPGP probes contain none of the seven affected legacy imports. The tools closure contains only MongoDB BSON-related imports, not the GSSAPI authentication packages identified by CVE-2026-2303. Full package/import/dependency/import-map and module identities are published as explicitly derived graphs. Original unfiltered output and failed attempts remain local because they contain host paths.

`source-continuity.json` records a separate comparison of the retained production graphs' source inputs: 10 relevant source/patch/lock directories, 72 tracked files, unchanged Git objects and current bytes. Dockerfile differences were reviewed separately. A fresh bootstrap dependency graph contained the same 337 package/import/module nodes. Its field `new_execution_or_compilation: false` refers to application execution/compilation; the read-only `go list` command did execute. This is not fresh compilation or proof that newly rebuilt ELF bytes match old ELF bytes.

To reproduce the public projection where the exact original local receipts remain available, run `python derive_review.py --root PATH_TO_CHECKOUT`. The generator performs no network, Git, alert, runtime or source mutation. It is provenance tooling, not an installation prerequisite.
