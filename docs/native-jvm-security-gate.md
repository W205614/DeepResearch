# Native JVM and complete image security gate

Trivy0.75.0 alone does not identify tarball JREs. `scripts/check_native_jvm.py` is an additional mandatory gate for the backend and Keycloak image matrices; it does not replace the normal full-image Trivy gate or lower any HIGH/CRITICAL threshold.

`deployment/security/native-jvm-tools.json` fixes official Syft1.55.0 and Grype0.121.0 Linux archive URLs, sizes and SHA256, cross-checked against official GitHub asset digests and publisher checksums. Linux extracts only the verified regular executable and compares cached executables against the verified archive on reuse. Windows uses separately fixed official executable SHA256.

The advisory database is obtained from the **static timestamped official Grype URL** in that manifest. `grype db import` verifies its checksum-qualified compressed blob, and the helper independently checks the extracted3.3GB SQLite database byte count and SHA256. `GRYPE_DB_AUTO_UPDATE=false` prevents drift. It never substitutes `latest` under the old hash. The database is downloaded to a runner cache rather than committed or uploaded as a giant CI artifact. Current snapshot expires after7days: CI fails until a reviewed manifest refresh records a new official static URL, compressed hash, extracted DB hash and observed date. Missing/offline/corrupt/expired database or tool downloads fail closed.

The helper creates an explicitly owned audit container without starting it. It reads the actual JRE with `docker cp` and verifies every regular file and symlink against a manifest generated from the fixed official Temurin21.0.13+9 archive. Thus an old ELF plus forged `release` text cannot pass. The audit container is retained in created state and recorded in `summary.json`; CI runners are ephemeral, and local cleanup remains an explicit ownership-reviewed operation.

It then saves native-only Syft and Grype reports, requiring exactly one actual JVM, correct version/source/ABI/path and a declared OpenJDK CPE. It additionally saves default **complete** Syft and Grype reports, blocks missing/unknown package versions, HIGH/CRITICAL/UNKNOWN findings, and ignored findings. Medium and low findings remain in the raw reports and summary. Tool/scan operational failures cannot be represented as empty successful reports.

CI uploads native/full SBOMs, scanner reports, configuration and summary separately from the Trivy raw scan. A passing local run is not a claim that GitHubCI has run or that production has been deployed. Current local receipts are under `docs/evidence/optimization-20261026/native-jvm-ci.json`.

Local Windows invocation using the verified existing scanner/cache:

```powershell
.venv/Scripts/python.exe scripts/check_native_jvm.py --image IMAGE --profile business-java --output-dir .cache/eval/native-jvm-unique --tool-dir .cache/optimization-20261026/vendor-candidates/native-jdk-scanners --db-cache .cache/optimization-20261026/vendor-candidates/native-jdk-scanners/grype-db
```

Use profile `keycloak` for the glibc Keycloak runtime. Output directories must be new; prior reports are preserved rather than silently reused or overwritten.
