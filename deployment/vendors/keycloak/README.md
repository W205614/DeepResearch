# Fixed Keycloak runtime

`Dockerfile.keycloak` retains the official26.8.1 application and UBI rootfs, uses RPM to uninstall the old JDK package and actual owned files in a build stage, then installs the signed, checksum-bound Temurin21.0.13+9 glibc JRE. Builder RPM tooling never enters the runtime. Existing non-JDK RPM names/versions, application files, runtime UID/ports/entrypoint are retained. New JAVA_HOME/PATH and Java executable links select the new JRE.

The release archive and publisher SBOM signatures were verified against the official Adoptium public-key fingerprint in `source.lock.json`. The source archive is fetched by immutable URL plus SHA256 at build time; no version-text editing or fake/devel component versions are used.

Trivy0.75 Java dependency scanning does **not** identify a tarball JRE. A full-image Trivy zero is insufficient by itself. Use Syft1.55.0 `--override-default-catalogers java-jvm-cataloger` on the actual image, require exactly the expected runtime and declared OpenJDK CPE, then scan that SBOM with Grype0.121.0 using a frozen database (`GRYPE_DB_AUTO_UPDATE=false`). Current scanner binary checksums and frozen DB provenance are in the evidence receipt. The synthetic old-JVM positive control verifies matching but is never actual-image evidence.

The original official H2 jar is repackaged by Quarkus; its1066 executable class files match the official MavenCentral2.4.240 artifact byte for byte. The verified actual component SBOM is supplied here for a supplemental scan with the same Trivy vulnerability DB; do not infer identity just from its filename. The3 launcher/generated outputs are classified as official Keycloak/Quarkus generated code with actual file hash inheritance, rather than fictional Maven packages.

Before replacing an existing26.4 realm, run the check/backup/apply/readback migration in `docs/keycloak-audience-migration.md`. Realm import does not update existing clients. Audience and issuer validation remain strict. Upgrade26.4→26.8 changes DB schema; rollback requires restoring the original DB backup into a separate database, never booting the old server against the migrated DB.

All current compatibility receipts use private synthetic database copies and actors. They do not prove production token continuity, federation, HA, TLS termination or capacity. No daily deployment is performed by these recipes or evidence tools.
