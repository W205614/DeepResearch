// Enumerate actual linked Go functions from a stripped ELF's runtime pclntab.
// This supplements module metadata; it does not prove absence of every possible
// optimized or inlined instruction and must not independently suppress a CVE.
package main

import (
	"crypto/sha256"
	"debug/elf"
	"debug/gosym"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
)

func main() {
	if len(os.Args) != 2 {
		panic("usage: inspect_gopcln <Go ELF>")
	}
	contents, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	hash := sha256.Sum256(contents)
	f, err := elf.Open(os.Args[1])
	if err != nil {
		panic(err)
	}
	defer f.Close()
	pcln := f.Section(".gopclntab")
	text := f.Section(".text")
	if pcln == nil || text == nil {
		panic("missing pclntab/text; do not interpret as an empty function set")
	}
	data, err := pcln.Data()
	if err != nil {
		panic(err)
	}
	table, err := gosym.NewTable(nil, gosym.NewLineTable(data, text.Addr))
	if err != nil {
		panic(err)
	}
	names := make([]string, 0, len(table.Funcs))
	for _, f := range table.Funcs {
		names = append(names, f.Name)
	}
	if len(names) == 0 {
		panic("empty function table; unsupported binary format")
	}
	sort.Strings(names)
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{
		"elf_sha256": hex.EncodeToString(hash[:]), "function_count": len(names),
		"linked_functions": names,
		"limitation": "pclntab functions only; compiler inlining is not exhaustively excluded",
	}); err != nil {
		panic(fmt.Errorf("write result: %w", err))
	}
}
