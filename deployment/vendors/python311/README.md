# Signed CPython 3.11.18 build

The official3.11.18 release (2026-10-25) supersedes3.11.15. DockerHub tags
`3.11.18-slim` and`3.11.18-slim-trixie` were absent at verification time.
The production Dockerfile therefore builds the signed official source through
pinned docker-library/python commit`83054588043b3a7ac1ce8324c9242164b632beb9`.
`Dockerfile.upstream` and its MIT license retain the reviewed original recipe.
`source-lock.json` freezes archive SHA256, public signer fingerprint and provenance.

CPython source is unmodified. The derived shell recipe keeps the same compiler
options, PGO/LTO, shared-library ABI and standard-library features. It bounds build
parallelism to2 and imports the pinned public release key locally. Archive bytes
are checked independently with SHA256 and GPG. Existing UV dependencies stay
frozen; the finalruntime still uses the original distrolessDebian13 recipe.

CPython is source-built rather than a Debian package. `prepare_python_runtime.py`
records its actual Python/SSL/Expat/SQLite identity and source lock separately
from the OS/PyPI package inventory. `scripts/verify_python_runtime.py` uses the
verified source archive's public upstream certificate fixtures to test rejected
compromised chains and accepted legal TLS entirely through MemoryBIO, plus the
specific tar link regression and direct document/native-library compatibility.
Fixtures are not production credentials and are not installed in the image.

A zerofinding Trivy report covers only recognized packages. It is never evidence
that an unrecognized CPython interpreter is patched; the signedsource lock,
actualruntime version and direct regression checks provide that separate proof.

Sources:
- https://www.python.org/downloads/release/python-31118/
- https://github.com/docker-library/python/blob/83054588043b3a7ac1ce8324c9242164b632beb9/3.11/slim-trixie/Dockerfile
- https://github.com/python/cpython/issues/160040
- https://github.com/python/cpython/issues/160097
