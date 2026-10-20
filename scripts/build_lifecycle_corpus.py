"""Freeze attributed public documentation and authored policy fixtures for acceptance.

Run explicitly; normal evaluation never refreshes the frozen corpus from the web.
"""
import hashlib
import json
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'eval/lifecycle'
PUBLIC = [
    ('configmap', 'configuration/configmap', 'ConfigMap 的用途、容量限制和保密能力是什么？', ['ConfigMap 用于保存非机密性数据', '不能提供保密或加密', '不能超过 1 MiB']),
    ('secret', 'configuration/secret', 'Secret 的用途是什么？默认存储是否已加密？', ['Secret 保存密码、令牌或密钥等敏感信息', '默认情况下在 etcd 中未加密存储']),
    ('pod-lifecycle', 'workloads/pods/pod-lifecycle', 'Pod 的 Running 阶段是否等于应用已经就绪？说明 Pod 阶段与运行状况的区别。', ['Running 表示 Pod 已经绑定节点并且至少一个容器在运行或启动重启中', '阶段是高层概述而非完整运行状况']),
    ('deployment', 'workloads/controllers/deployment', 'Deployment 管理什么对象，如何实现声明式更新？', ['Deployment 为 Pod 和 ReplicaSet 提供声明式更新', '控制器使实际状态逐步变成期望状态']),
    ('statefulset', 'workloads/controllers/statefulset', 'StatefulSet 为有状态应用提供哪些稳定标识？', ['管理有状态应用', '提供稳定的网络标识和持久存储']),
    ('job', 'workloads/controllers/job', 'Job 如何判断工作完成，Pod 失败后会怎样？', ['创建一个或多个 Pod 并跟踪成功完成数量', 'Pod 失败时 Job 可以创建新的 Pod 重试']),
    ('quota', 'policy/resource-quotas', 'ResourceQuota 在什么范围约束哪些资源？', ['按命名空间限制资源使用总量', '可以限制对象数量和计算资源总量']),
    ('network-policy', 'services-networking/network-policies', 'NetworkPolicy 的作用是什么，是否只创建对象就一定生效？', ['控制 Pod 的网络流量', '需要支持网络策略的网络插件实施']),
    ('persistent-volume', 'storage/persistent-volumes', 'PV 和 PVC 分别是什么，PV 生命周期是否依赖单个 Pod？', ['PV 是集群中的存储资源', 'PVC 是用户的存储请求', 'PV 生命周期独立于使用它的 Pod']),
    ('probes', 'workloads/pods/pod-lifecycle', '就绪探针与存活探针分别影响什么？', ['存活探针决定是否重启容器', '就绪探针决定是否接收流量']),
]


def write_lf(path, text):
    """Generated text has deterministic UTF-8/LF bytes on every host."""
    path.write_bytes(text.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8'))


def document_hashes(path):
    raw = path.read_bytes()
    normalized = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
    return {'sha256': hashlib.sha256(raw).hexdigest(),
            'sha256_definition': 'SHA-256 of exact frozen file bytes',
            'normalized_text_sha256': hashlib.sha256(normalized).hexdigest(),
            'normalized_text_sha256_definition':
                'SHA-256 of UTF-8 text with CRLF/CR normalized to LF; historical sha256 definition'}


def save(name, value):
    write_lf(TARGET / name, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def expand_glossary_definitions(text, commit, load_bytes):
    """Resolve content includes at the parent's immutable commit, never a moving branch.

    The caller stores the exact dependency bytes. This affects research evidence
    only; raw indexed documents and the separate public sample stay unchanged.
    """
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Glossary dependencies require an immutable commit SHA')
    dependencies, loaded = [], {}

    def replace(match):
        attrs = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', match.group(1)))
        term, length = attrs.get('term_id', ''), attrs.get('length', 'short')
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', term) or length not in {'all', 'short'}:
            raise ValueError('Invalid glossary definition include')
        url = f'https://raw.githubusercontent.com/kubernetes/website/{commit}/content/zh-cn/docs/reference/glossary/{term}.md'
        if term not in loaded:
            raw = load_bytes(url)
            if not isinstance(raw, bytes):
                raise TypeError('Glossary loader must preserve original bytes')
            loaded[term] = raw
            dependencies.append({'term_id': term, 'url': url, 'upstream_commit': commit,
                                 'license': 'CC-BY-4.0', 'sha256': hashlib.sha256(raw).hexdigest(),
                                 'path': f'evidence-includes/{term}.md'})
        body = loaded[term].decode('utf-8-sig')
        body = re.sub(r'^---\s*\n[\s\S]*?\n---\s*\n', '', body, count=1)
        if length == 'short':
            body = body.split('<!--more-->', 1)[0]
        body = re.sub(r'<!--[\s\S]*?-->', '', body).strip()
        if not body or re.search(r'\{\{<\s*glossary_definition\b', body):
            raise ValueError('Empty or recursive glossary definition')
        return body

    expanded = re.sub(r'\{\{<\s*glossary_definition\b([^{}]*?)>\}\}', replace, text)
    return expanded, dependencies


def policy(name, index, version):
    limit = 500 + index * 100 + (300 if version == 2 else 0)
    facts = [
        f'{name}第{version}版适用于正式员工，不适用于实习生。',
        f'{name}单次报销上限为 {limit} 元，不得将上限理解为自动获批金额。',
        f'申请必须在事项发生后 {15 + index} 日内提交，逾期须补充书面说明。',
        '申请人不得审核自己的申请，审批人须具有当前有效的授权。',
        '交通费用须提供日期、出发地、目的地和实际支付凭证。',
        '住宿费用按实际支付核验；资料未规定不同城市的额外补贴。',
        '餐饮费用仅覆盖审批范围内的工作支出，不包含个人消费。',
        '表格中的金额单位为人民币元，次数单位为次，不能混用。',
        '补充材料不能替代原始支付凭证，缺失时应标记待补件。',
        '加急处理仅改变处理顺序，不豁免金额、身份和审核要求。',
        '重复提交同一事项不产生两笔支付，补充说明关联原申请。',
        '授权撤销后停止新的审批，已有记录保留时间和审核人。',
        '申请被驳回应说明原因，重新提交时引用原申请并保留差异。',
        '不同文件出现冲突时须由负责人核对，不按发布时间猜测适用关系。',
        '本资料未提供处理效率、满意度或全年节省金额，不能据此推断收益。',
        '资料仅用于合成工程验收，不是任何真实企业的报销制度。',
    ]
    if version == 2:
        facts.append(f'{name}第2版明确替代第1版；新申请使用第2版，历史记录保留原适用版本。')
    text = f'# {name} 第{version}版\n\n' + '\n\n'.join(
        f'## 第{i + 1}条\n\n{fact}\n\n执行记录应关联申请编号、适用版本、证据位置和当前处理状态；缺少证据不得推断已完成。'
        for i, fact in enumerate(facts))
    return text, limit, facts


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / 'documents').mkdir(exist_ok=True)
    corpus, provenance, chains, cases, retrieval = [], [], [], [], []
    for index in range(20):
        name = f'合成专项制度{index + 1:02d}'
        version = 2 if index < 6 else 1
        text, limit, facts = policy(name, index, version)
        filename = f'policy-{index + 1:02d}.md'
        document_path = TARGET / 'documents' / filename
        write_lf(document_path, text)
        corpus.append({'name': filename, 'path': 'documents/' + filename})
        provenance.append({'name': filename, 'kind': 'authored-synthetic', **document_hashes(document_path)})
        if index < 6:
            old, _, _ = policy(name, index, 1)
            oldpath = f'documents/policy-{index + 1:02d}-v1.archive.md'
            write_lf(TARGET / oldpath, old)
            chains.append({'document': filename, 'old': oldpath, 'current': 'documents/' + filename})
        questions = [
            (f'根据{name}第{version}版，单次报销上限是多少，实习生是否适用？', [f'上限为{limit}元', '不适用于实习生']),
            (f'根据{name}第{version}版，加急申请能否免除金额、身份和审核要求？', ['加急仅改变处理顺序', '不豁免金额、身份和审核要求']),
        ]
        for number, (question, expected) in enumerate(questions):
            case_id = f'policy-{index + 1:02d}-{number + 1}'
            cases.append({'id': case_id, 'critical': True, 'question': question,
                          'evidence': [{'id': 'L-fixture', 'title': filename, 'text': text}],
                          'expected_facts': expected, 'forbidden_claims': ['实习生适用本制度', '加急免除审核'],
                          'must_refuse': False, 'provenance': 'Authored synthetic policy; no real enterprise claims.'})
            retrieval.append({'id': case_id, 'query': question, 'relevant_documents': [filename]})
    client = httpx.Client(timeout=40, follow_redirects=True)
    commit_response = client.get('https://api.github.com/repos/kubernetes/website/commits/main')
    commit_response.raise_for_status()
    commit = commit_response.json()['sha']
    samples, include_provenance = [], {}
    (TARGET / 'evidence-includes').mkdir(exist_ok=True)

    def include_bytes(url):
        dependency = client.get(url)
        dependency.raise_for_status()
        dependency_path = TARGET / 'evidence-includes' / url.rsplit('/', 1)[-1]
        dependency_path.write_bytes(dependency.content)
        return dependency_path.read_bytes()

    for slug, path, question, expected in PUBLIC:
        source = f'https://raw.githubusercontent.com/kubernetes/website/{commit}/content/zh-cn/docs/concepts/{path}.md'
        response = client.get(source)
        response.raise_for_status()
        text = re.sub(r'<!--[\s\S]*?-->', '', response.text)
        text = re.sub(r'^---[\s\S]*?---\s*', '', text)
        filename = f'public-{slug}.txt'
        frozen = f'# 冻结公开资料：{slug}\n\n来源：{source}\n\n许可：Kubernetes 文档 CC BY 4.0；保留原作者归属。\n\n{text}'
        document_path = TARGET / 'documents' / filename
        write_lf(document_path, frozen)
        corpus.append({'name': filename, 'path': 'documents/' + filename})
        provenance.append({'name': filename, 'kind': 'public-documentation', 'url': source, 'upstream_commit': commit,
                           'license': 'CC-BY-4.0', **document_hashes(document_path)})
        case = {'id': 'public-' + slug, 'critical': True, 'question': question,
                'evidence': [{'id': 'W-public', 'title': filename, 'text': text}],
                'expected_facts': expected, 'forbidden_claims': [], 'must_refuse': False,
                'provenance': source}
        research_text, dependencies = expand_glossary_definitions(text, commit, include_bytes)
        research_case = case
        if dependencies:
            research_case = {**case, 'evidence': [{**case['evidence'][0], 'text': research_text}],
                             'evidence_includes': dependencies}
            include_provenance.update({entry['term_id']: entry for entry in dependencies})
        cases.append(research_case)
        sample_questions = {
            'configmap': ('通过环境变量和 subPath 使用 ConfigMap 时，更新是否自动生效？', ['环境变量不会自动更新，需要重启 Pod', 'subPath 挂载不会收到 ConfigMap 更新']),
            'secret': ('Secret 中的 base64 编码是否等于加密？', ['base64 编码并不提供保密性']),
            'pod-lifecycle': ('删除 Pod 时默认终止宽限期是多少？', ['默认终止宽限期为30秒']),
            'deployment': ('Deployment 的 revisionHistoryLimit 默认保留多少个旧 ReplicaSet？', ['默认保留10个旧 ReplicaSet']),
            'statefulset': ('StatefulSet 的 Pod 网络标识依赖哪类 Service？', ['需要无头服务负责 Pod 的网络标识']),
            'job': ('Job 的 parallelism 与 completions 分别控制什么？', ['parallelism 控制并行 Pod 数', 'completions 指定成功完成次数']),
            'quota': ('违反 ResourceQuota 的创建或更新请求会收到什么响应？', ['返回403 Forbidden并说明违反的约束']),
            'network-policy': ('多个 NetworkPolicy 是否按先后顺序覆盖，还是累积允许？', ['网络策略是累积的', '允许范围取适用策略的并集']),
            'persistent-volume': ('PV 的 Retain 与 Delete 回收策略有什么差别？', ['Retain 支持管理员手动回收', 'Delete 删除 PV 及对应存储资产']),
            'probes': ('配置启动探针后，存活和就绪探针何时开始？', ['启动探针成功之前不会执行存活与就绪探针']),
        }
        sample_question, sample_expected = sample_questions[slug]
        samples.append({**case, 'id': 'sample-' + slug, 'question': sample_question, 'expected_facts': sample_expected})
        retrieval.append({'id': case['id'], 'query': question, 'relevant_documents': [filename]})
    # Partition is frozen before execution. Real sample answers remain separately
    # recorded; the public-source sample is not an independent human blind review.
    save('corpus.json', corpus)
    save('provenance.json', provenance)
    save('version_cases.json', chains)
    save('development.json', cases[:30])
    save('holdout.json', cases[30:])
    save('research_cases.json', cases)
    save('public_sample.json', samples)
    save('retrieval_cases.json', retrieval)
    save('evidence-includes/provenance.json', list(include_provenance.values()))
    print(json.dumps({'documents': len(corpus), 'version_chains': len(chains), 'development': 30,
                      'holdout': len(cases[30:]), 'public_source_sample': len(samples), 'upstream_commit': commit}))


if __name__ == '__main__':
    main()
