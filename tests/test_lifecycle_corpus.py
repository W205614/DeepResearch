"""Pure fixture construction checks; no model or network calls."""
import hashlib

import pytest

from scripts.build_lifecycle_corpus import expand_glossary_definitions


COMMIT = 'a' * 40


def test_research_definition_uses_same_commit_and_preserves_dependency_hash():
    raw = '---\ntitle: 示例\n---\n\n非机密数据。<!--more-->\n完整限制。'.encode()
    urls = []

    def load(url):
        urls.append(url)
        return raw

    original = '{{< glossary_definition term_id="example" length="all" >}}\n原始正文。'
    rendered, deps = expand_glossary_definitions(original, COMMIT, load)
    assert rendered == '非机密数据。\n完整限制。\n原始正文。'
    assert 'glossary_definition' in original  # Pure transformation, raw input unchanged.
    assert urls == [f'https://raw.githubusercontent.com/kubernetes/website/{COMMIT}/content/zh-cn/docs/reference/glossary/example.md']
    assert deps[0]['sha256'] == hashlib.sha256(raw).hexdigest()
    assert deps[0]['upstream_commit'] == COMMIT


def test_repeated_include_loads_once_and_short_stops_at_more_marker():
    calls = []
    text = '{{< glossary_definition length="short" term_id="sample-term" >}}\n{{< glossary_definition term_id="sample-term" length="all" >}}'
    rendered, deps = expand_glossary_definitions(text, COMMIT, lambda url: calls.append(url) or b'short<!--more-->long')
    assert rendered == 'short\nshortlong'
    assert len(calls) == len(deps) == 1


@pytest.mark.parametrize('commit,term', [('main', 'safe'), (COMMIT, '../unsafe')])
def test_moving_commit_and_path_escape_fail_without_fetch(commit, term):
    def load(_url):
        pytest.fail('Invalid dependency must never fetch')

    with pytest.raises(ValueError):
        expand_glossary_definitions('{{< glossary_definition term_id="' + term + '" >}}', commit, load)


def test_raw_without_definition_include_is_byte_equivalent_and_not_fetched():
    original = '原始正文\n{{< glossary_tooltip term_id="safe" text="名称" >}}\n'
    rendered, deps = expand_glossary_definitions(original, COMMIT, lambda _: pytest.fail('Unexpected fetch'))
    assert rendered.encode() == original.encode()
    assert deps == []


def test_document_hashes_distinguish_frozen_bytes_from_normalized_text(tmp_path):
    from scripts.build_lifecycle_corpus import document_hashes

    path = tmp_path / 'frozen.txt'
    raw = '原文\r\n条件\r\n'.encode('utf-8')
    path.write_bytes(raw)
    hashes = document_hashes(path)
    assert hashes['sha256'] == hashlib.sha256(raw).hexdigest()
    assert hashes['normalized_text_sha256'] == hashlib.sha256('原文\n条件\n'.encode()).hexdigest()
    assert hashes['sha256'] != hashes['normalized_text_sha256']
    assert path.read_bytes() == raw  # Metadata calculation cannot rewrite a measured input.


def test_generated_text_and_json_have_platform_independent_lf_bytes(tmp_path, monkeypatch):
    from scripts import build_lifecycle_corpus as builder

    monkeypatch.setattr(builder, 'TARGET', tmp_path)
    path = tmp_path / 'generated.txt'
    builder.write_lf(path, '第一行\r\n第二行\r第三行\n')
    assert path.read_bytes() == '第一行\n第二行\n第三行\n'.encode('utf-8')
    hashes = builder.document_hashes(path)
    assert hashes['sha256'] == hashes['normalized_text_sha256']
    builder.save('metadata.json', {'source': '中文'})
    assert b'\r' not in (tmp_path / 'metadata.json').read_bytes()
