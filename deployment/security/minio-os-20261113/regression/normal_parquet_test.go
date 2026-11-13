package parquet

import (
	"bytes"
	"io"
	"os"
	"reflect"
	"testing"

	parquetgo "github.com/fraugster/parquet-go"
	parquettypes "github.com/fraugster/parquet-go/parquet"
	"github.com/fraugster/parquet-go/parquetschema"
	jsonfmt "github.com/minio/minio/internal/s3select/json"
)

type normalReadCloser struct{ *bytes.Reader }

func (normalReadCloser) Close() error { return nil }

func TestNormalThrift025ParquetRoundTrip(t *testing.T) {
	schema, err := parquetschema.ParseSchemaDefinition("message normal { required int64 id; optional binary label (UTF8); }")
	if err != nil {
		t.Fatal(err)
	}
	var data bytes.Buffer
	writer := parquetgo.NewFileWriter(&data, parquetgo.WithSchemaDefinition(schema),
		parquetgo.WithCompressionCodec(parquettypes.CompressionCodec_UNCOMPRESSED))
	if err := writer.AddData(map[string]interface{}{"id": int64(7), "label": []byte("normal-value")}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := NewParquetReader(normalReadCloser{bytes.NewReader(data.Bytes())}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	record, err := reader.Read(nil)
	if err != nil {
		t.Fatal(err)
	}
	actual := map[string]interface{}{}
	for _, field := range record.(*jsonfmt.Record).KVS {
		actual[field.Key] = field.Value
	}
	if !reflect.DeepEqual(actual, map[string]interface{}{"id": int64(7), "label": "normal-value"}) {
		t.Fatalf("ordinary Parquet row changed: %#v", actual)
	}
	if _, err := reader.Read(nil); err != io.EOF {
		t.Fatalf("expected EOF after one complete row, got %v", err)
	}
	if err := os.WriteFile("/out/normal-parquet.parquet", data.Bytes(), 0644); err != nil {
		t.Fatal(err)
	}
}
