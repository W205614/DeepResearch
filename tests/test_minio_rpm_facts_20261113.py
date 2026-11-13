import sqlite3
import struct

import pytest

from scripts.minio_rpm_facts_20261113 import UPDATED, database_facts, header_facts, header_fields


def package_blob(name, *, installed=1794541224, transaction=1794541224, extra=b'signed-metadata'):
    version, release, arch = UPDATED.get(name, ('10.40', '6.el9', 'x86_64'))
    values = [(1000, 6, name.encode() + b'\0'), (1001, 6, version.encode() + b'\0'),
              (1002, 6, release.encode() + b'\0'), (1022, 6, arch.encode() + b'\0'),
              (1008, 4, struct.pack('>I', installed)),
              (1128, 4, struct.pack('>I', transaction)), (6000, 7, extra)]
    data = bytearray()
    indexes = []
    for tag, kind, raw in values:
        if kind == 4:
            data.extend(b'\0' * (-len(data) % 4))
        indexes.append(struct.pack('>IIII', tag, kind, len(data), len(raw) if kind == 7 else 1))
        data.extend(raw)
    return struct.pack('>II', len(indexes), len(data)) + b''.join(indexes) + data


def database(path, *, clock=1794541224):
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE Packages(hnum INTEGER PRIMARY KEY, blob BLOB NOT NULL);
        CREATE TABLE Installtid(key BLOB NOT NULL, hnum INTEGER NOT NULL, idx INTEGER NOT NULL);
        CREATE TABLE Unrelated(key BLOB NOT NULL, hnum INTEGER NOT NULL);
    ''')
    for number, name in enumerate([*UPDATED, 'pcre2'], 1):
        value = clock if name in UPDATED else 1694541224
        connection.execute('INSERT INTO Packages VALUES (?,?)',
                           (number, package_blob(name, installed=value, transaction=value)))
        connection.execute('INSERT INTO Installtid VALUES (?,?,0)', (struct.pack('<I', value), number))
    connection.execute('INSERT INTO Unrelated VALUES (?,1)', (b'exact-index-payload',))
    connection.commit()
    connection.close()
    return path


def test_actual_observation_fields_alone_do_not_change_complete_header():
    first, observed = header_facts(package_blob('glibc'))
    second, newer = header_facts(package_blob('glibc', installed=1794541225))
    assert first == second
    assert observed['INSTALLTIME'] + 1 == newer['INSTALLTIME']


def test_every_other_header_byte_remains_bound():
    first, _ = header_facts(package_blob('glibc'))
    second, _ = header_facts(package_blob('glibc', extra=b'different-signature-metadata'))
    assert first != second


def test_other_packages_do_not_receive_clock_exception():
    first, observed = header_facts(package_blob('pcre2'))
    second, newer = header_facts(package_blob('pcre2', installed=1794541225))
    assert first != second
    assert observed == newer == {}


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown_kind', 'offset', 'length', 'truncated'])
def test_malformed_header_fails(mutation):
    raw = bytearray(package_blob('glibc'))
    if mutation == 'duplicate':
        struct.pack_into('>I', raw, 8 + 16, 1000)
    elif mutation == 'unknown_kind':
        struct.pack_into('>I', raw, 8 + 4, 10)
    elif mutation == 'offset':
        struct.pack_into('>I', raw, 8 + 8, len(raw) + 1)
    elif mutation == 'length':
        struct.pack_into('>I', raw, 4, len(raw))
    else:
        raw = raw[:7]
    with pytest.raises(ValueError):
        header_fields(bytes(raw))


def test_identity_version_cannot_use_clock_exception():
    raw = package_blob('glibc').replace(b'290.el9_9', b'291.el9_9')
    with pytest.raises(ValueError, match='NEVRA'):
        header_facts(raw)


def test_complete_database_all_rows_and_headers_same_except_observations(tmp_path):
    first = database_facts(database(tmp_path / 'first.sqlite'))
    second = database_facts(database(tmp_path / 'second.sqlite', clock=1794541225))
    assert first['raw_sha256'] != second['raw_sha256']
    assert first['stable'] == second['stable']
    assert first['stable_sha256'] == second['stable_sha256']
    assert first['install_observations'] != second['install_observations']
    assert first['package_count'] == 5
    assert first['table_count'] == 3


@pytest.mark.parametrize('sql', [
    'CREATE TABLE Extra(payload BLOB)',
    'CREATE INDEX ExtraIndex ON Unrelated(key)',
    "UPDATE Unrelated SET key=x'666f72676564'",
    "INSERT INTO Unrelated VALUES (x'00',2)",
])
def test_schema_and_every_nonclock_row_remain_bound(tmp_path, sql):
    path = database(tmp_path / 'db.sqlite')
    first = database_facts(path)
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
    assert database_facts(path)['stable_sha256'] != first['stable_sha256']


@pytest.mark.parametrize('sql', [
    "UPDATE Installtid SET key=x'00000000' WHERE hnum=1",
    'UPDATE Installtid SET idx=1 WHERE hnum=1',
    'DELETE FROM Packages WHERE hnum=1',
    'INSERT INTO Packages SELECT 6,blob FROM Packages WHERE hnum=1',
])
def test_clock_index_and_updated_package_closure_fail_closed(tmp_path, sql):
    path = database(tmp_path / 'db.sqlite')
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
    with pytest.raises(ValueError):
        database_facts(path)


def test_unchanged_header_mutation_is_not_masked(tmp_path):
    path = database(tmp_path / 'db.sqlite')
    first = database_facts(path)
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE Packages SET blob=? WHERE hnum=5',
                           (package_blob('pcre2', installed=1),))
    assert database_facts(path)['stable_sha256'] != first['stable_sha256']


@pytest.mark.parametrize('name,value', [('user_version', 1), ('application_id', 12345678)])
def test_persistent_application_metadata_is_bound_after_writer_closes(tmp_path, name, value):
    path = database(tmp_path / 'db.sqlite')
    first = database_facts(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute('PRAGMA ' + name + '=' + str(value))
        connection.commit()
    finally:
        connection.close()
    second = database_facts(path)
    assert first['stable']['application_metadata'][name] == 0
    assert second['stable']['application_metadata'][name] == value
    assert first['raw_sha256'] != second['raw_sha256']
    assert first['stable_sha256'] != second['stable_sha256']
