# Scoped storage bootstrap

This short-lived deployment program only creates the two required buckets and
reconciles two fixed IAM users/policies. It has no HTTP server, general admin CLI,
model call, dynamic policy input or persisted root credential. Application and
Milvus credentials must differ from each other and the root credential.

The scratch image runs as UID 10001. `go.mod`/`go.sum` lock the official
[MinIO Admin SDK](https://github.com/minio/madmin-go) and
[MinIO S3 SDK](https://github.com/minio/minio-go) plus their dependencies; upstream
licenses remain applicable (the admin SDK declares AGPL-3.0). The source build
replaces the old precompiled general-purpose `mc` client after its embedded
dependencies failed the security gate. The image still requires independent
security scanning on the final delivery SHA.

`SetPolicy` is intentionally used with the deployed MinIO-compatible builtin
user API to replace the user's policy set, rather than retaining a previously
broader policy. Repeated bootstrap must preserve existing objects. Bucket names
match the project's existing defaults; custom deployments must verify their
actual bucket/prefix before changing this program.
