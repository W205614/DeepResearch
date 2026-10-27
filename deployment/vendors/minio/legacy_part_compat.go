package cmd

import "github.com/tinylib/msgp/msgp"

// decodePartInfoCompatible keeps the2024 persisted multipart FileInfo tuple
// readable after the upstream2026 switch to ObjectPartInfo maps. It changes
// no data on disk and never treats zero/foreign/corrupt metadata as a valid part.
func decodePartInfoCompatible(data []byte, number int) (*ObjectPartInfo, error) {
	current := &ObjectPartInfo{}
	rest, err := current.UnmarshalMsg(data)
	if err == nil && len(rest) == 0 && current.Number == number && current.ETag != "" && current.Size >= 0 && current.ActualSize >= 0 {
		return current, nil
	}
	n, rest, err := msgp.ReadArrayHeaderBytes(data)
	if err != nil || n != 28 {
		return nil, errFileCorrupt
	}
	// RELEASE.2024-05-28 FileInfo tuple: Parts is field17 (zero-based).
	for i := 0; i < 17; i++ {
		rest, err = msgp.Skip(rest)
		if err != nil {
			return nil, errFileCorrupt
		}
	}
	count, rest, err := msgp.ReadArrayHeaderBytes(rest)
	if err != nil || count != 1 {
		return nil, errFileCorrupt
	}
	fields, rest, err := msgp.ReadMapHeaderBytes(rest)
	if err != nil || fields > 32 {
		return nil, errFileCorrupt
	}
	normalized := msgp.AppendMapHeader(nil, fields)
	aliases := map[string]string{"ETag": "e", "Number": "n", "Size": "s", "ActualSize": "as", "ModTime": "mt", "index": "i", "crc": "crc"}
	for i := uint32(0); i < fields; i++ {
		var key string
		key, rest, err = msgp.ReadStringBytes(rest)
		if err != nil {
			return nil, errFileCorrupt
		}
		if alias, ok := aliases[key]; ok {
			key = alias
		}
		normalized = msgp.AppendString(normalized, key)
		tail, skipErr := msgp.Skip(rest)
		if skipErr != nil {
			return nil, errFileCorrupt
		}
		normalized = append(normalized, rest[:len(rest)-len(tail)]...)
		rest = tail
	}
	// Consume every remaining tuple field, rejecting truncated/trailing data.
	for i := 18; i < 28; i++ {
		rest, err = msgp.Skip(rest)
		if err != nil {
			return nil, errFileCorrupt
		}
	}
	if len(rest) != 0 {
		return nil, errFileCorrupt
	}
	current = &ObjectPartInfo{}
	tail, err := current.UnmarshalMsg(normalized)
	if err != nil || len(tail) != 0 || current.Number != number || current.ETag == "" || current.Size < 0 || current.ActualSize < 0 {
		return nil, errFileCorrupt
	}
	return current, nil
}
