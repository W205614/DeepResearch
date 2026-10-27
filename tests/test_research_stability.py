from backend.domain.models import Analysis, Evidence, Verification
from backend.research.graph import ResearchGraph
from backend.research.evidence_policy import validate_claim
from backend.research.reporting import assemble_report, quoted_facts
from backend.domain.models import ReportOutline, SourceFacts


def source(text="内部试点中，40名参与者完成了任务恢复。这只是内部试点，不能推断外部用户表现。"):
    return Evidence(id="one", kind="web", title="record", text=text, access="fulltext",
                    url="https://source.example/", trust_label="authored fixture").model_dump()


def test_comparison_cannot_be_attributed_to_each_original_record():
    records = [{**source('同一批次记录甲为12名。尚未核对。'), 'id': 'a'},
               {**source('同一批次记录乙为15名。尚未核对。'), 'id': 'b'}]
    assert not validate_claim('两份记录均指出同一批次参与者人数存在冲突。', records)[0]
    assert validate_claim('对照记录甲12名与记录乙15名，人数存在差异，不能确定单一真值。', records)[0]
    assert validate_claim('两份记录均指出人数存在冲突。', [source('人数存在冲突。')])[0]


async def test_writer_cannot_expand_facts_or_drop_an_omitted_fact(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    facts = [{"text": "根据资料，甲方案26名参与者完成。", "source_ids": ["one"]},
             {"text": "根据资料，乙方案31名参与者完成。", "source_ids": ["two"]}]

    async def ask(role, instruction, data, schema, run_id):
        return schema.model_validate({"title": "比较", "sections": [{"heading": "结果",
            "claim_indices": [0, 0, 999], "claims": [{
                "text": "两份原文均指出结果冲突；统计口径为试点成功人数。", "source_ids": ["one", "two"]}]}]})

    graph.ask = ask
    result = await graph.writer({"run_id": "outline", "topic": "比较数量", "plan": {},
        "claims": facts, "evidence": [source()], "gaps": [], "conflicts": []})
    actual = [c for section in result["draft"]["sections"] for c in section["claims"]]
    assert actual == facts


async def test_empty_analysis_recovers_only_complete_verbatim_source_facts(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    calls = []

    async def ask(role, instruction, data, schema, run_id):
        calls.append(role)
        if role == "analyst":
            return Analysis(claims=[], gaps=["缺少地区编号"])
        return schema.model_validate({"facts": [
            {"source_id": "one", "quote": "内部试点中，40名参与者完成了任务恢复。"},
            {"source_id": "one", "quote": "这只是内部试点，不能推断外部用户表现。"},
            {"source_id": "one", "quote": "40名参与者完成了任务恢复。"},
            {"source_id": "one", "quote": "41名参与者完成了任务恢复。"},
            {"source_id": "unknown", "quote": "内部试点中，40名参与者完成了任务恢复。"}]})

    graph.ask = ask
    result = await graph.analyst({"run_id": "extract", "topic": "说明完成情况和适用范围", "plan": {"questions": ["地区编号？"]},
                                  "evidence": [source()], "conflicts": []})
    assert calls == ["analyst", "evidence_recovery"]
    assert len(result["claims"]) == 2
    assert "40名" in result["claims"][0]["text"]
    assert "不能推断外部" in result["claims"][1]["text"]
    assert result["gaps"] == ["缺少地区编号"]


async def test_rejected_evidence_gets_one_quote_recovery_without_approving_it(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    calls = []

    async def ask(role, instruction, data, schema, run_id):
        calls.append(role)
        if role == 'judge':
            return schema.model_validate({'accepted_ids': [], 'notes': ['缺少地区编号']})
        return schema.model_validate({'facts': [{'source_id': 'one', 'quote': source()['text']}]})

    graph.ask = ask
    result = await graph.judge({'run_id': 'judge-recovery', 'topic': '说明完成情况与范围', 'plan': {},
                                'web_results': [source()], 'local_results': []})
    assert calls == ['judge', 'evidence_recovery']
    assert [item['id'] for item in result['evidence']] == ['one']
    assert len(result['recovered_claims']) == 1
    assert 'validation' not in result  # Exact quotes are not automatic support approval.
    assert result['limitations'] == []


def test_recovery_cannot_strip_a_negative_or_a_scope_prefix():
    record = source('仅内部试点中，40名参与者未完成任务恢复。')
    extracted = SourceFacts.model_validate({'facts': [
        {'source_id': 'one', 'quote': '40名参与者未完成任务恢复。'},
        {'source_id': 'one', 'quote': '仅内部试点中，40名参与者完成任务恢复。'},
    ]})
    assert quoted_facts(extracted, [record]) == []


def test_assembly_keeps_all_distinct_facts_within_schema_limits():
    facts = [{'text': f'fact {i}', 'source_ids': ['one']} for i in range(40)]
    # Duplicated and unknown indices cannot multiply or discard the facts.
    outline = ReportOutline(sections=[{'question_index': i % 5, 'claim_indices': [i, i, -1, 500]}
                                      for i in range(8)])
    result = assemble_report('question', [f'question {i}' for i in range(5)], facts, outline)
    actual = [c.model_dump() for section in result.sections for c in section.claims]
    assert sorted(actual, key=lambda c: int(c['text'].split()[1])) == facts
    assert len(result.sections) <= 8 and all(len(s.claims) <= 12 for s in result.sections)


async def test_research_roles_use_explicit_low_variance_sampling(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    seen = []

    async def structured(role, instruction, data, schema, run_id, **options):
        seen.append(options)
        return Analysis()

    graph.providers.structured = structured
    await graph.ask('analyst', 'facts', {}, Analysis, 'temperature')
    assert seen == [{'temperature': 0}]


async def test_coverage_claims_without_validator_indices_keep_their_own_schema(runtime, monkeypatch):
    from unittest.mock import AsyncMock
    from backend.domain.models import QuestionCoverage
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    model = AsyncMock(return_value=QuestionCoverage(answered_questions=[0], original_request_answered=True))
    monkeypatch.setattr(runtime.providers, 'structured', model)
    data = {'topic': '金额', 'questions': ['上限?'],
            'claims': [{'text': '上限800元', 'source_ids': ['W1']}]}
    result = await graph.ask('coverage', 'check final coverage', data, QuestionCoverage, 'coverage-no-index')
    assert result.original_request_answered and result.answered_questions == [0]
    assert model.call_args.args[2] == data
    assert await runtime.db.rows('SELECT * FROM verified_claims WHERE run_id=?', ('coverage-no-index',)) == []


async def test_repair_does_not_rejudge_unchanged_approved_facts(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    batches = []

    async def ask(role, instruction, data, schema, run_id):
        if role == "repair":
            return schema.model_validate({"repairs": [{"index": 1,
                "replacement": {"text": "repaired fact", "source_ids": ["one"]}}]})
        if role == "coverage":
            return schema.model_validate({"answered_questions": [0], "original_request_answered": True})
        batches.append([c["index"] for c in data["claims"]])
        return Verification(checks=[{"index": c["index"], "supported": c["text"] != "bad fact"
            and not (len(batches) > 1 and c["index"] == 0), "reason": "verdict", "atomic_checks": [{"text": c["text"], "supported": True}]} for c in data["claims"]])

    graph.ask = ask
    result = await graph.validator({"run_id": "repair-preserve", "topic": "facts", "user_id": "alice",
        "plan": {"questions": ["facts?"]}, "gaps": [], "conflicts": [],
        "evidence": [source("approved fact. repaired fact.")], "draft": {"title": "facts", "sections": [
            {"heading": "facts", "claims": [{"text": t, "source_ids": ["one"]} for t in ["approved fact", "bad fact"]]}]}})
    assert batches == [[0, 1], [1]]
    assert "approved fact" in result["report"] and "repaired fact" in result["report"]
    assert result["validation"]["unanswered_questions"] == []

def test_long_chinese_evidence_retains_amount_negation_and_version_scope():
    from backend.research.graph import evidence_context
    source='\n'.join(['合成报销制度第2版',
        '第一条：本制度适用于正式员工，不适用于实习生。',
        '第二条：单次报销上限为800元，不得理解为自动获批金额。',
        *[f'第{i}条：归档材料请按规定保存，备案工作人员负责顺序整理，不改变业务处理口径。' for i in range(3,45)],
        '第四十五条：第2版替代第1版，历史记录保留原适用版本。'])
    result=evidence_context([{'id':'policy','text':source}],text_limit=750,total_limit=750,
        query='根据报销制度第2版，单次报销上限是多少，实习生是否适用？')[0]
    assert '800元' in result['text']
    assert '不适用于实习生' in result['text']
    assert '不得理解为自动获批金额' in result['text']
    assert '第2版替代第1版' in result['text']
    assert len(result['text'].encode())<=750 and result['excerpt_truncated']

def test_numeric_evidence_normalizes_spacing_and_thousands_without_substring_matches():
    from backend.research.evidence_policy import validate_claim
    def local(text): return [{'id':'L-policy','kind':'local','text':text}]
    assert validate_claim('根据所提供资料，上限1100元。',local('上限为 1,100 元。'))[0]
    assert validate_claim('根据所提供资料，上限1,100 元。',local('上限为1100元。'))[0]
    assert validate_claim('根据所提供资料，上限1,100,000元。',local('上限为 1100000 元。'))[0]
    assert not validate_claim('根据所提供资料，上限12元。',local('上限120元。'))[0]
    assert not validate_claim('根据所提供资料，数量12。',local('数量120。'))[0]
    assert not validate_claim('根据所提供资料，上限1100美元。',local('上限1100元。'))[0]
    assert not validate_claim('根据所提供资料，比例20%。',local('比例0.2。'))[0]

def test_omission_claim_cannot_deny_explicit_status_code_in_its_source():
    from backend.research.evidence_policy import validate_claim
    source=[{'text':'如果创建或更新违反约束，控制平面使用HTTP状态码 403 Forbidden拒绝请求。','kind':'web','access':'fulltext','trust_label':'fixture'}]
    assert not validate_claim('对照资料推断超限将拒绝；该推断边界是资料未给出具体状态码与插件完整触发链路。',source)[0]
    assert validate_claim('资料明确说明使用HTTP状态码403拒绝请求。',source)[0]
    assert validate_claim('资料未给出具体状态码。',[{'text':'资料未给出具体状态码。'}])[0]
    assert validate_claim('资料未给出具体状态码。',[{'text':'HTTP接口延迟500毫秒；返回码未披露。'}])[0]

def test_structured_atomic_check_cannot_be_overridden_by_overall_yes():
    from backend.domain.models import Verification
    from backend.research.graph import normalize_verification,supported_indices
    verdict=Verification.model_validate({'checks':[
        {'index':0,'supported':True,'reason':'核心事实正确但尾部缺失断言不支持',
         'atomic_checks':[{'text':'上限为800元','supported':True},{'text':'原文没有列出金额','supported':False}]},
        {'index':1,'supported':True,'reason':'省略必要的复核条件','omitted_conditions':['还必须经过另一位审核人复核']},
        {'index':2,'supported':True,'reason':'无依据的来源完整性自述','unsupported_parts':['原文没有完整列出其他方式']},
        {'index':3,'supported':True,'reason':'两项条件完整','atomic_checks':[{'text':'同时满足A和B','supported':True}]}]})
    claims={0:"上限为800元，原文没有列出金额",1:"条件A",2:"元断言",3:"同时满足A和B"}
    normalized=normalize_verification(verdict,claims)
    assert supported_indices(verdict,{0,1,2,3},claims)=={3}
    assert [check.supported for check in normalized.checks]==[False,False,False,True]
    assert verdict.checks[0].supported is True  # No mutation of cached/input verdict.

async def test_atomic_rejection_is_persisted_before_partial_recovery(runtime,monkeypatch):
    from unittest.mock import AsyncMock
    from backend.domain.models import Verification
    from backend.research.graph import ResearchGraph
    graph=ResearchGraph(runtime.settings,runtime.db,runtime.providers,runtime.vectors,runtime.documents)
    verdict=__import__('backend.domain.models',fromlist=['TargetVerification']).TargetVerification.model_validate({'checks':[{'index':0,'supported':True,'reason':'丢失必要条件','atomic_checks':[{'target_id':'0:0','supported':True}],
        'omitted_conditions':['必须同时具有复核记录']} ]})
    monkeypatch.setattr(runtime.providers,'structured',AsyncMock(return_value=verdict))
    result=await graph.ask('validator','atomic support',{'claims':[{'index':0,'text':'只需A','source_ids':['W1']}],
        'sources':{'W1':{'id':'W1','kind':'web','text':'同时满足A和B'}}},Verification,'atomic-run')
    assert not result.checks[0].supported
    stored=await runtime.db.one('SELECT result FROM verified_claims WHERE run_id=?',('atomic-run',))
    import json
    assert json.loads(stored['result'])['verdict']['checks'][0]['supported'] is False


def test_atomic_spans_reject_source_rewrite_unchecked_tail_and_empty_legacy_checks():
    from backend.research.graph import normalize_verification, supported_indices
    claims = {0: '四种方式：命令、文件、API，以及资料中第四种方式。',
              1: '上限为800元，但自动获批。', 2: '完整原文。', 3: '甲为19名，乙为24名。'}
    checks = Verification.model_validate({'checks': [
        {'index': 0, 'supported': True, 'reason': 'source is correct', 'atomic_checks': [
            {'text': '命令、环境变量、文件、API', 'supported': True}]},
        {'index': 1, 'supported': True, 'reason': 'only checked the first clause', 'atomic_checks': [
            {'text': '上限为800元', 'supported': True}]},
        {'index': 2, 'supported': True, 'reason': 'legacy empty checks'},
        {'index': 3, 'supported': True, 'reason': 'exact sequential spans', 'atomic_checks': [
            {'text': '甲为19名', 'supported': True}, {'text': '乙为24名', 'supported': True}]}]})
    result = normalize_verification(checks, claims)
    assert supported_indices(result, set(claims), claims) == {3}
    assert all(not check.verification_complete for check in result.checks[:3])
    assert all(not check.unsupported_parts for check in result.checks[:3])
    assert checks.checks[0].supported  # Stored original remains immutable.


def test_atomic_spans_allow_only_whitespace_and_prose_separator_gaps():
    from backend.domain.models import AtomicClaimCheck
    from backend.research.graph import atomic_span_error
    def atoms(*texts):
        return [AtomicClaimCheck(text=text, supported=True) for text in texts]
    assert not atomic_span_error('上限为 1,100 元。\n不得自动获批。', atoms('上限为1,100元', '不得自动获批'))
    assert atomic_span_error('下降-20%。', atoms('下降', '20'))  # Signs and percentages remain mandatory.
    assert atomic_span_error('先A，再B。', atoms('再B', '先A'))
    assert atomic_span_error('A。', atoms('A', 'A'))
    assert not atomic_span_error('根据所提供资料，甲为19名，乙为24名。', atoms('甲为19名', '乙为24名'))
    assert atomic_span_error('根据所提供资料，不允许自动获批。', atoms('允许自动获批'))
    assert atomic_span_error('对照资料可推断：A。', atoms('A'))


async def test_partial_recovery_ignores_legacy_contract_even_when_verdict_would_anchor(runtime):
    import json
    from backend.research.graph import VERIFICATION_CONTRACT
    run = await runtime.create('alice', __import__('backend.domain.models', fromlist=['RunRequest']).RunRequest(
        topic='legacy cache boundary', client_request_id='legacy-cache-contract'))
    await runtime.cancel(run['id'], 'alice')
    data = {'verdict': {'checks': [{'index': 0, 'supported': True, 'reason': 'old',
        'atomic_checks': [{'target_id': '0:0', 'text': '旧结论', 'supported': True}]}]},
        'input': {'claims': [{'index': 0, 'text': '旧结论', 'source_ids': ['W-old']}],
                  'sources': {'W-old': {'id': 'W-old', 'kind': 'web', 'text': '旧结论'}}}}
    await runtime.db.execute('INSERT INTO verified_claims(run_id,cache_key,result) VALUES(?,?,?)',
                             (run['id'], 'old-contract', json.dumps(data)))
    report, sources = await runtime.partial_report(run['id'])
    assert (report, sources) == ('', [])
    data['contract_version'] = VERIFICATION_CONTRACT
    data['wire_version'] = __import__('backend.research.graph',fromlist=['VERIFICATION_WIRE']).VERIFICATION_WIRE
    await runtime.db.execute('UPDATE verified_claims SET result=? WHERE run_id=? AND cache_key=?',
                             (json.dumps(data), run['id'], 'old-contract'))
    report, sources = await runtime.partial_report(run['id'])
    assert '旧结论' in report and len(sources) == 1
