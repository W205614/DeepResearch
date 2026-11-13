"""Bind logical SQL contents and header bytes, separating two clock fields for four RPMs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import struct


UPDATED = {
    'glibc': ('2.34', '290.el9_9', 'x86_64'),
    'glibc-common': ('2.34', '290.el9_9', 'x86_64'),
    'glibc-minimal-langpack': ('2.34', '290.el9_9', 'x86_64'),
    'libgcc': ('11.5.0', '17.el9_9', 'x86_64'),
}
CLOCK_TAGS = {1008: 'INSTALLTIME', 1128: 'INSTALLTID'}


def header_fields(blob: bytes):
    if len(blob) < 8:
        raise ValueError('Truncated RPM header')
    count, size = struct.unpack_from('>II', blob)
    start = 8 + count * 16
    if start + size != len(blob):
        raise ValueError('Complete RPM header length differs')
    fields = {}
    widths = {0: 0, 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1}
    for index in range(count):
        tag, kind, offset, length = struct.unpack_from('>IIII', blob, 8 + index * 16)
        if tag in fields or kind not in range(10) or offset > size:
            raise ValueError('Duplicate, unknown or out-of-bounds RPM field')
        begin = start + offset
        if kind in widths:
            end = begin + widths[kind] * length
        else:
            end = begin
            for _ in range(length):
                end = blob.find(b'\0', end)
                if end < 0:
                    raise ValueError('Unterminated RPM string field')
                end += 1
        if end > len(blob) or (kind == 6 and length != 1):
            raise ValueError('RPM field exceeds complete header')
        fields[tag] = (kind, length, begin, end)
    return fields


def header_facts(blob: bytes):
    fields = header_fields(blob)

    def string(tag):
        kind, length, begin, end = fields[tag]
        if kind != 6 or length != 1:
            raise ValueError('RPM identity is not a single string')
        return blob[begin:end - 1].decode('utf-8')

    name = string(1000)
    identity = {str(tag): string(tag) for tag in (1001, 1002, 1022) if tag in fields}
    stable = bytearray(blob)
    clocks = {}
    if name in UPDATED:
        if tuple(identity.get(str(tag)) for tag in (1001, 1002, 1022)) != UPDATED[name]:
            raise ValueError('Updated RPM NEVRA differs from the signed inputs')
        for tag, label in CLOCK_TAGS.items():
            kind, length, begin, end = fields[tag]
            if kind != 4 or length != 1 or end - begin != 4:
                raise ValueError('Install observation is not exactly one uint32')
            clocks[label] = struct.unpack_from('>I', blob, begin)[0]
            stable[begin:end] = b'\0' * 4
    # Indexes, padding, immutable regions and every other header byte remain bound.
    return {'name': name, 'identity': identity, 'stable_header_hex': stable.hex()}, clocks


def _value(kind, value):
    if kind in ('blob', 'text'):
        return {'sqlite_type': kind, 'hex': value.hex()}
    if kind in ('integer', 'null'):
        return {'sqlite_type': kind, 'value': value}
    raise ValueError('Unexpected SQLite value type in the RPM database')


def database_facts(path: Path):
    with path.open('rb') as stream:
        raw_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.text_factory = bytes
    try:
        if connection.execute('PRAGMA integrity_check').fetchall() != [(b'ok',)]:
            raise ValueError('RPM SQLite integrity check failed')
        schema = connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall()
        schema_facts = [[None if v is None else v.hex() for v in row] for row in schema]
        packages = {}
        observations = {}
        seen_updated = set()
        for hnum, blob in connection.execute('SELECT hnum,blob FROM Packages ORDER BY hnum'):
            facts, clocks = header_facts(blob)
            if facts['name'] in seen_updated:
                raise ValueError('Duplicate updated package header')
            if clocks:
                seen_updated.add(facts['name'])
                observations[hnum] = {'name': facts['name'], **clocks}
            packages[hnum] = facts
        if seen_updated != set(UPDATED):
            raise ValueError('Signed update package set is incomplete')
        tables = {}
        names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        for (raw_name,) in names:
            name = raw_name.decode('ascii')
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [row[1].decode('ascii') for row in connection.execute('PRAGMA table_info(' + quoted + ')')]
            selected = []
            for column in columns:
                escaped = '"' + column.replace('"', '""') + '"'
                selected.extend(['typeof(' + escaped + ')', escaped])
            rows = []
            for raw_row in connection.execute('SELECT ' + ','.join(selected) + ' FROM ' + quoted):
                values = {column: (raw_row[index * 2].decode('ascii'), raw_row[index * 2 + 1])
                          for index, column in enumerate(columns)}
                if name == 'Packages':
                    if columns != ['hnum', 'blob']:
                        raise ValueError('RPM Packages columns differ')
                    hnum = values['hnum'][1]
                    row = {'hnum': _value(*values['hnum']), 'blob': packages[hnum]}
                elif name == 'Installtid':
                    if columns != ['key', 'hnum', 'idx']:
                        raise ValueError('RPM Installtid columns differ')
                    hnum = values['hnum'][1]
                    if hnum in observations:
                        if (values['key'] != ('blob', struct.pack('<I', observations[hnum]['INSTALLTID']))
                                or values['idx'] != ('integer', 0)):
                            raise ValueError('Installtid index does not bind the actual header observation')
                        values['key'] = ('blob', b'\0' * 4)
                    row = {column: _value(*values[column]) for column in columns}
                else:
                    row = {column: _value(*values[column]) for column in columns}
                rows.append(row)
            tables[name] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, separators=(',', ':')))
        application_metadata = {name: connection.execute('PRAGMA ' + name).fetchone()[0]
                                for name in ('user_version', 'application_id')}
        stable = {'schema': schema_facts, 'tables': tables,
                  'application_metadata': application_metadata}
        encoded = json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()
        return {'raw_sha256': raw_hash, 'stable_sha256': hashlib.sha256(encoded).hexdigest(),
                'stable': stable, 'install_observations': observations,
                'package_count': len(packages), 'table_count': len(tables)}
    finally:
        connection.close()
