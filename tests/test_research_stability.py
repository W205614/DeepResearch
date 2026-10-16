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


async def test_repair_does_not_rejudge_unchanged_approved_facts(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    batches = []

    async def ask(role, instruction, data, schema, run_id):
        if role == "repair":
            return schema.model_validate({"repairs": [{"index": 1,
                "replacement": {"text": "repaired fact", "source_ids": ["one"]}}]})
        if role == "coverage":
            return schema.model_validate({"answered_questions": [0]})
        batches.append([c["index"] for c in data["claims"]])
        return Verification(checks=[{"index": c["index"], "supported": c["text"] != "bad fact"
            and not (len(batches) > 1 and c["index"] == 0), "reason": "verdict"} for c in data["claims"]])

    graph.ask = ask
    result = await graph.validator({"run_id": "repair-preserve", "topic": "facts", "user_id": "alice",
        "plan": {"questions": ["facts?"]}, "gaps": [], "conflicts": [],
        "evidence": [source("approved fact. repaired fact.")], "draft": {"title": "facts", "sections": [
            {"heading": "facts", "claims": [{"text": t, "source_ids": ["one"]} for t in ["approved fact", "bad fact"]]}]}})
    assert batches == [[0, 1], [1]]
    assert "approved fact" in result["report"] and "repaired fact" in result["report"]
    assert result["validation"]["unanswered_questions"] == []
