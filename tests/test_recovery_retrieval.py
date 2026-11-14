import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call

import pytest

from scripts import recovery


@pytest.fixture
def milvus(monkeypatch):
    def install(collections, *, integer_collections=()):
        client = SimpleNamespace()

        def query(*, collection_name, filter, output_fields, limit):
            assert call(collection_name) in client.load_collection.call_args_list
            if collection_name in integer_collections and filter == 'id != ""':
                raise TypeError('Cannot compare Int64 primary key with VarChar')
            assert filter == ''
            assert output_fields == ['id', 'vector', 'user_id']
            assert limit == 1
            return collections[collection_name][:limit]

        def search(*, collection_name, data, filter, limit, output_fields):
            row = collections[collection_name][0]
            assert data == [row['vector']]
            assert filter == 'user_id == ' + json.dumps(row['user_id'])
            assert limit == 1
            assert output_fields == ['user_id']
            return [[{'id': row['id'], 'entity': {'user_id': row['user_id']}}]]

        client.list_collections = Mock(return_value=list(collections))
        client.load_collection = Mock()
        client.query = Mock(side_effect=query)
        client.search = Mock(side_effect=search)
        module = ModuleType('pymilvus')
        module.MilvusClient = Mock(return_value=client)
        monkeypatch.setitem(sys.modules, 'pymilvus', module)
        return client

    return install


def run_probe():
    exec(compile(recovery.RESTORED_RETRIEVAL, '<retrieval-recovery-probe>', 'exec'), {})


def vector_row(primary_key):
    return {'id': primary_key, 'vector': [0.25, 0.75], 'user_id': 'owner-"quoted"\\scope'}


@pytest.mark.parametrize('primary_key', ['chunk-1', 42], ids=['varchar', 'int64'])
@pytest.mark.parametrize('empty_first', [True, False], ids=['empty-before', 'empty-after'])
def test_empty_int64_collection_does_not_prevent_owner_scoped_vector_search(
    milvus, capsys, primary_key, empty_first,
):
    entries = [('empty_int64', []), ('business', [vector_row(primary_key)])]
    if not empty_first:
        entries.reverse()
    collections = dict(entries)
    integer_collections = ['empty_int64'] + (['business'] if isinstance(primary_key, int) else [])
    client = milvus(collections, integer_collections=integer_collections)

    run_probe()

    assert client.load_collection.call_args_list == [call(name) for name in collections]
    assert [item.kwargs['collection_name'] for item in client.query.call_args_list] == list(collections)
    client.search.assert_called_once()
    assert json.loads(capsys.readouterr().out) == {
        'nonempty_collections_searched': 1, 'results_checked': 1,
    }


def test_every_nonempty_collection_is_searched(milvus, capsys):
    collections = {'varchar': [vector_row('chunk-1')], 'int64': [vector_row(42)]}
    client = milvus(collections, integer_collections=['int64'])

    run_probe()

    assert client.load_collection.call_args_list == [call(name) for name in collections]
    assert [item.kwargs['collection_name'] for item in client.search.call_args_list] == list(collections)
    assert json.loads(capsys.readouterr().out) == {
        'nonempty_collections_searched': 2, 'results_checked': 2,
    }


@pytest.mark.parametrize('collections', [
    {}, {'empty_int64': []}, {'empty_varchar': []}, {'empty_int64': [], 'empty_varchar': []},
], ids=['no-collections', 'empty-int64', 'empty-varchar', 'all-empty'])
def test_no_nonempty_collection_fails_closed(milvus, capsys, collections):
    client = milvus(collections, integer_collections=['empty_int64'])

    with pytest.raises(AssertionError, match='No nonempty vector collection'):
        run_probe()

    assert client.load_collection.call_args_list == [call(name) for name in collections]
    assert client.query.call_count == len(collections)
    client.search.assert_not_called()
    assert not capsys.readouterr().out


@pytest.mark.parametrize('hits,error', [
    ([], IndexError),
    ([[]], AssertionError),
    ([[{'entity': {'user_id': 'another-owner'}}]], AssertionError),
], ids=['no-query-results', 'no-hits', 'wrong-owner'])
def test_missing_search_hits_or_wrong_owner_fails_closed(milvus, capsys, hits, error):
    client = milvus({'business': [vector_row('chunk-1')]})
    client.search.side_effect = None
    client.search.return_value = hits

    with pytest.raises(error):
        run_probe()

    client.search.assert_called_once()
    assert not capsys.readouterr().out


@pytest.mark.parametrize('operation', ['load_collection', 'query', 'search'])
def test_collection_errors_propagate_even_after_a_successful_search(milvus, capsys, operation):
    client = milvus({'first': [vector_row('chunk-1')], 'second': [vector_row(42)]},
                    integer_collections=['second'])
    method = getattr(client, operation)
    original = method.side_effect
    failure = RuntimeError(f'{operation} failed')

    def fail_second(*args, **kwargs):
        name = args[0] if args else kwargs['collection_name']
        if name == 'second':
            raise failure
        return original(*args, **kwargs) if original is not None else None

    method.side_effect = fail_second
    with pytest.raises(RuntimeError) as raised:
        run_probe()

    assert raised.value is failure
    assert client.search.call_args_list[0].kwargs['collection_name'] == 'first'
    assert not capsys.readouterr().out
