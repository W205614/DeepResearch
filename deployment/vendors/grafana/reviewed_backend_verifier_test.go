package signature

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"

	"github.com/grafana/grafana/pkg/plugins"
	"github.com/grafana/grafana/pkg/plugins/log"
)

func TestMaintainerReviewedBackendIntegrity(t *testing.T) {
	dir := t.TempDir()
	manifest := []byte("synthetic fixed manifest")
	backend := []byte("synthetic rebuilt backend")
	checksum := func(raw []byte) string { sum := sha256.Sum256(raw); return hex.EncodeToString(sum[:]) }
	write := func(name string, raw []byte) {
		if err := os.WriteFile(filepath.Join(dir, name), raw, 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("MANIFEST.txt", manifest)
	write("backend_linux_amd64", backend)
	write("module.js", []byte("modified frontend"))
	original := checksum([]byte("original backend"))
	key := "integrity-fixture/backend_linux_amd64"
	reviewedBackendHashes[key] = reviewedBackend{original, checksum(backend), checksum(manifest)}
	defer delete(reviewedBackendHashes, key)
	plugin := plugins.FoundPlugin{JSONData: plugins.JSONData{ID: "integrity-fixture"}, FS: plugins.NewLocalFS(dir)}
	replacement, ok := reviewedBackendHash(plugin, "backend_linux_amd64", original)
	if !ok || verifyHash(log.New("fixture"), plugin, "backend_linux_amd64", replacement) != nil {
		t.Fatal("exact rebuilt backend rejected")
	}
	for _, path := range []string{"module.js", "unknown", "../backend_linux_amd64", "/backend_linux_amd64"} {
		if _, ok := reviewedBackendHash(plugin, path, original); ok {
			t.Fatalf("unexpected override for %q", path)
		}
	}
	if verifyHash(log.New("fixture"), plugin, "module.js", checksum([]byte("original frontend"))) == nil {
		t.Fatal("tampered frontend accepted")
	}
	write("backend_linux_amd64", []byte("tampered rebuilt backend"))
	if verifyHash(log.New("fixture"), plugin, "backend_linux_amd64", replacement) == nil {
		t.Fatal("tampered backend accepted")
	}
	write("MANIFEST.txt", []byte("modified manifest"))
	if _, ok := reviewedBackendHash(plugin, "backend_linux_amd64", original); ok {
		t.Fatal("different manifest accepted")
	}
	unsigned := plugins.FoundPlugin{JSONData: plugins.JSONData{ID: "integrity-fixture"}, FS: plugins.NewLocalFS(t.TempDir())}
	if _, ok := reviewedBackendHash(unsigned, "backend_linux_amd64", original); ok {
		t.Fatal("unsigned input acquired a backend override")
	}
}
