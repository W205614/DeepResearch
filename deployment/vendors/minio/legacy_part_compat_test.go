package cmd

import (
	"os"
	"testing"
)

func TestPinnedLegacyPart2024PreservesCompleteMetadata(t *testing.T) {
	raw, err := os.ReadFile("testdata/deepresearch-legacy-part2024.msgp")
	if err != nil {
		t.Fatal(err)
	}
	part, err := decodePartInfoCompatible(raw, 1)
	if err != nil {
		t.Fatal(err)
	}
	if part.ETag != "79b281060d337b9b2b84ccf390adcf74" || part.Number != 1 || part.Size != 5<<20 || part.ActualSize != 5<<20 || part.ModTime.IsZero() || part.Checksums["CRC32"] != "r/zBbw==" {
		t.Fatalf("lost legacy multipart metadata: %+v", part)
	}
	encoded, err := part.MarshalMsg(nil)
	if err != nil {
		t.Fatal(err)
	}
	if again, err := decodePartInfoCompatible(encoded, 1); err != nil || again.ETag != part.ETag {
		t.Fatal("current-format roundtrip failed", err)
	}
	for _, bad := range [][]byte{raw[:len(raw)/2], append(append([]byte{}, raw...), 0), []byte{0xc0}} {
		if _, err := decodePartInfoCompatible(bad, 1); err == nil {
			t.Fatal("accepted corrupt or trailing metadata")
		}
	}
	if _, err := decodePartInfoCompatible(raw, 2); err == nil {
		t.Fatal("accepted another part's metadata")
	}
}
