package csv

import (
	"bufio"
	"bytes"
	"io"
	"testing"
)

type boundedProbeReader struct{ emitted int }

func (r *boundedProbeReader) Read(p []byte) (int, error) {
	// A synthetic 1GiB record without allocating it. The patched reader must
	// stop near1MiB, before it can consume this advertised stream.
	if r.emitted >= 1<<30 {
		return 0, io.EOF
	}
	for i := range p {
		p[i] = 'a'
	}
	r.emitted += len(p)
	return len(p), nil
}
func TestPinnedCVE202639414StopsUnterminatedRecord(t *testing.T) {
	source := &boundedProbeReader{}
	r := Reader{buf: bufio.NewReaderSize(source, 4096)}
	_, err := r.nextSplit(csvSplitSize, nil)
	failure, ok := err.(*s3Error)
	if !ok || failure.code != "OverMaxRecordSize" {
		t.Fatalf("unexpected error: %v", err)
	}
	if source.emitted > maxCharsPerRecord+4096 {
		t.Fatalf("unbounded read consumed%d", source.emitted)
	}
}
func TestPinnedCVE202639414PreservesLegalLongRecord(t *testing.T) {
	input := append(bytes.Repeat([]byte{'a'}, maxCharsPerRecord), byte('\n'))
	r := Reader{buf: bufio.NewReaderSize(bytes.NewReader(input), 4096)}
	value, err := r.nextSplit(csvSplitSize, nil)
	if err != nil || !bytes.Equal(value, input) {
		t.Fatalf("legal1MiB record failed: %v", err)
	}
}
