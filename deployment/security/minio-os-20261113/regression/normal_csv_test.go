package csv

import (
	"bufio"
	"bytes"
	"io"
	"testing"
)

func TestNormalNativeRepairPreservesValidCSV(t *testing.T) {
	for _, input := range [][]byte{[]byte("alpha,beta\n"), []byte("alpha,\"quoted value\"\n")} {
		reader := Reader{buf: bufio.NewReaderSize(bytes.NewReader(input), 4096)}
		actual, err := reader.nextSplit(csvSplitSize, nil)
		if err != io.EOF || !bytes.Equal(actual, input) {
			t.Fatalf("ordinary complete CSV record changed: %q, %v", actual, err)
		}
		if remaining, err := reader.nextSplit(csvSplitSize, nil); err != io.EOF || len(remaining) != 0 {
			t.Fatalf("expected an empty final block, got %q, %v", remaining, err)
		}
	}
}
