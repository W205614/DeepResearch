# Agent OpenSSL path review

This is an exact application-path review, not a claim that Debian's installed OpenSSL package has been patched. The five raw `UNKNOWN` findings remain in the scan. The upstream advisory additionally classifies them as Low. Its fixes were public on 10 November 2026, with the corresponding formal releases scheduled for 1 December 2026.

`new-findings-source-review.json` binds the actual portable image config, ordered layers, runtime configuration, all 515 ELF files and all 5972 runtime Python files. Each of the five records has an explicit current-path disposition. A changed library, application, effective engine/provider setting, protocol or command requires another review; this evidence does not grant future arbitrary FFI or new features.

## Repeat the isolated probes

Use the Agent immutable reference from the actual final publication receipt, and first verify its raw portable config digest is `sha256:fbde5203f64cf35014e27a93717462506384e7b9afdb89b69ede5d2706d326bf` and its ordered diff IDs match the review. An Engine ID is a local representation and must not substitute for that chain.

Feed each supplied probe script to the image's Python process with `-B -`, using `docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges --entrypoint /opt/venv/bin/python <verified-immutable-reference> -B -`. These probes use no credentials, business mounts, published ports or provider calls. They do not start the day-to-day stack.

Expected observations are in `loaded-probe.json` and `boundary-probe.json`. The former distinguishes the system Debian OpenSSL from cryptography's static implementation and psycopg's separately named bundled libraries. The latter invokes the installed implementation to check nonce rejection, a valid 12-byte AEAD round trip and rejection of an EC PKCS7 recipient before encryption. These are contract observations, not a claim of upstream fixed code.

## Repeat the closure and route inspection

Use the repository's `scripts/check_agent_full_scope.py` and `scripts/agent_full_scope_probe.py` to recheck the actual image/config and complete native/Python closures. The expected closure digests are in the review and inventories. The final scan must keep all packages, all severities and secret scanning; every new or changed tuple still blocks the exact review.

For the route audit, inspect every regular ELF from a pristine export of that same image with GNU `readelf --wide --dynamic --dyn-syms`. Compare each ELF SHA-256, `DT_NEEDED`, `SONAME`, search paths and selected imports with `native-route-inventory.json`. Full dynamic import lists were examined; their digests and original tool digest are recorded. An absence of a symbol or a string alone is not the proof: combine it with the pinned CPython TLS-method selection, exact Python call-site inventory, actual API contracts and effective configuration.

The system libssl's only external consumer is CPython `_ssl`, selecting TLS methods rather than the separate QUIC method family. The system libcrypto consumers are that TLS path, hashing and the three recorded engines. There is no Agent `openssl` CLI or image `openssl.cnf`; current application code does not select the affected CMS or AES-GCM-SIV paths. cryptography's exposed API definitions are explicitly recorded rather than hidden, and its private OpenSSL is not confused with the Debian DSOs.

`primary-sources.json` records official URLs, source snapshots' digests and exact public fix commits. Original full advisory/HTML/patch downloads and the private failed optional documentation transfer remain outside this public evidence set. `cpython-source-selection.json` ties the method selection to the already verified Python 3.11.18 source archive.

This evidence does not itself execute the updated gate, change the profile, authorize publication, or prove general model quality or production capacity. Those results require their own actual receipts.
