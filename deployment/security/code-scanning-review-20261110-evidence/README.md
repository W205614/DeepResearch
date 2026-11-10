# Code-scanning technical review, 2026-11-10

The per-alert register is [review.json](review.json): 65 Trivy rows, 60 with technical evidence limited to current selected production build profiles, five standalone module contexts still pending. These are automated technical facts, not a claim of human independent review or human risk acceptance. Archived vulnerable manifest snapshots and unselected recipes are not generally declared safe.

This task reparsed 27 retained complete graphs: 14 existing Grafana profiles remain referenced in their existing tracked locations; 13 other profiles are supplied here as **derived complete package/module/import projections**. The projections are not original raw scanner or `go list` output. They retain every package node, every `Imports`, `Deps`, `ImportMap`, and every module identity/version/checksum/replacement field. Only local filesystem metadata and unrelated file lists were removed to avoid publishing host paths. Original compressed and decompressed SHA-256, node counts, original receipt identities, original ELF identity, source-lock bindings, GOOS/GOARCH/CGO, and completion evidence are retained.

Original raw graphs and original receipts remain locally retained, not rewritten. Native build metadata and official advisory responses are copied byte-for-byte after checking they contain no host absolute paths or private payload patterns. Source-lock identities were rechecked against the current checkout. The historical graph/native module sets match; this does not claim fresh compilation, fresh ELF extraction, a new image, or safety for other build profiles.

The Milvus original completion source is a retained independent technical review explicitly recording both original command exits, not a newly obtained raw machine receipt. This distinction is preserved in both derived receipts.

The current primary OpenPGP advisory is <https://vuln.go.dev/ID/GO-2026-5932.json>. Its 2026-11-02 revision added a fixed module event; old statements that no fixed module exists are not reused. The advisory still identifies seven deprecated OpenPGP import paths. The complete corresponding production closures do not import those paths. Current native Grafana module identities select x/net v0.62.0 and go-jose v4.1.6; CNA affected-version records are included verbatim.

## Reproduction and input boundary

`input-whitelist.json` fixes the exact relative local metadata inputs and SHA-256 values. It does not contain credentials or absolute host paths. Run `python derive_review.py --root PATH_TO_CHECKOUT` where those original local evidence files are retained. The generator verifies every frozen input and each current source lock before deriving the public outputs, with no network/Git/alert/runtime mutation. The generator and whitelist are provenance material; the original local cache is intentionally not bundled as a clean-checkout prerequisite.

| Profile | Full package nodes | Original compressed bytes | Public derived compressed bytes |
|---|---:|---:|---:|
| alertmanager | 739 | 203775 | 122146 |
| amtool | 705 | 182510 | 99969 |
| storage-bootstrap | 337 | 76527 | 28598 |
| etcd | 569 | 144883 | 76645 |
| etcdctl | 493 | 115436 | 55152 |
| etcdutl | 477 | 109394 | 50498 |
| loki | 2179 | 2019134 | 1820309 |
| prometheus | 1518 | 482918 | 299451 |
| promtool | 1368 | 418636 | 251206 |
| tempo | 2076 | 1114447 | 939027 |
| otel | 871 | 243390 | 143399 |
| milvus_main | 2147 | 2334765 | 2301198 |
| milvus_parser | 818 | 224931 | 174191 |
