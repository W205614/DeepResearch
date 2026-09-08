from backend.research.routing import decide


def test_only_manual_research_mode_bypasses_semantic_routing():
    explicit = decide("quick", "请研究生成式 AI 风险")
    assert explicit and explicit.mode == "quick" and explicit.signals == ("explicit_mode",)
    assert decide("auto", "现在开始你叫 DeepResearch") is None
    assert decide("auto", "上一个问题是什么？") is None
    assert decide("auto", "请做一份生成式 AI 行业趋势与竞争研究") is None
