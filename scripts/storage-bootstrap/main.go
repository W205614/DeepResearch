// One-shot scoped IAM/bootstrap client. It has no HTTP server or general CLI.
package main

import (
 "context"
 "fmt"
 "os"
 "time"

 madmin "github.com/minio/madmin-go/v3"
 "github.com/minio/minio-go/v7"
 "github.com/minio/minio-go/v7/pkg/credentials"
)

const objectsPolicy = `{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket"],"Resource":["arn:aws:s3:::deepresearch-documents"]},{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":["arn:aws:s3:::deepresearch-documents/uploads/*","arn:aws:s3:::deepresearch-documents/quarantine/*"]}]}`
const milvusPolicy = `{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketMultipartUploads"],"Resource":["arn:aws:s3:::a-bucket"]},{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":["arn:aws:s3:::a-bucket/*"]}]}`

func bootstrap() error {
 rootUser, rootSecret := os.Getenv("MINIO_ROOT_USER"), os.Getenv("MINIO_ROOT_PASSWORD")
 objectSecret, milvusSecret := os.Getenv("OBJECT_STORE_SECRET_KEY"), os.Getenv("MILVUS_STORAGE_PASSWORD")
 if rootUser == "" || rootSecret == "" || len(objectSecret) < 16 || len(milvusSecret) < 16 || objectSecret == milvusSecret || objectSecret == rootSecret || milvusSecret == rootSecret {
  return fmt.Errorf("independent credentials required")
 }
 ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
 defer cancel()
 endpoint := os.Getenv("MINIO_BOOTSTRAP_ENDPOINT")
 if endpoint == "" { endpoint = "minio:9000" }
 admin, err := madmin.New(endpoint, rootUser, rootSecret, false)
 if err != nil { return err }
 storage, err := minio.New(endpoint, &minio.Options{Creds:credentials.NewStaticV4(rootUser, rootSecret, ""), Secure:false})
 if err != nil { return err }
 for _, bucket := range []string{"deepresearch-documents", "a-bucket"} {
  exists, err := storage.BucketExists(ctx, bucket)
  if err != nil { return err }
  if !exists { if err = storage.MakeBucket(ctx, bucket, minio.MakeBucketOptions{Region:"us-east-1"}); err != nil { return err } }
 }
 for _, identity := range []struct{ name, secret, policy string }{{"dr-objects", objectSecret, objectsPolicy}, {"dr-milvus", milvusSecret, milvusPolicy}} {
  if err = admin.AddUser(ctx, identity.name, identity.secret); err != nil { return err }
  if err = admin.AddCannedPolicy(ctx, identity.name, []byte(identity.policy)); err != nil { return err }
  // Replace the user's policy set, rather than retaining an accidental broader grant.
  if err = admin.SetPolicy(ctx, identity.name, identity.name, false); err != nil { return err }
 }
 return nil
}

func main() {
 if err := bootstrap(); err != nil {
  fmt.Fprintln(os.Stderr, "Scoped storage bootstrap failed; private SDK output withheld")
  os.Exit(1)
 }
 fmt.Println("Scoped storage identities prepared")
}
