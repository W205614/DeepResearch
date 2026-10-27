package signature

import (
	"crypto/sha256"
	"encoding/hex"
	"io"

	"github.com/grafana/grafana/pkg/plugins"
)

// Maintainer-built backends have a different trust root than upstream assets.
// The generated map is compiled into this exact Grafana build. It has no
// environment, configuration or runtime expansion mechanism.
type reviewedBackend struct {
	OriginalSHA256 string
	RebuiltSHA256  string
	ManifestSHA256 string
}

func reviewedBackendHash(plugin plugins.FoundPlugin, file, originalHash string) (string, bool) {
	reviewed, exists := reviewedBackendHashes[plugin.JSONData.ID+"/"+file]
	if !exists || originalHash != reviewed.OriginalSHA256 {
		return "", false
	}
	// Calculate has already verified the upstream PGP signature. Also bind this
	// replacement to the complete original manifest from the pinned base image.
	manifest, err := plugin.FS.Open("MANIFEST.txt")
	if err != nil {
		return "", false
	}
	defer manifest.Close()
	raw, err := io.ReadAll(io.LimitReader(manifest, 1024*1024+1))
	if err != nil || len(raw) > 1024*1024 {
		return "", false
	}
	sum := sha256.Sum256(raw)
	if hex.EncodeToString(sum[:]) != reviewed.ManifestSHA256 {
		return "", false
	}
	return reviewed.RebuiltSHA256, true
}
