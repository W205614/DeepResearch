from backend.research.routing import assistant_name_setting, decide


def test_explicit_and_high_confidence_routes_use_rules():
    explicit = decide("quick", "请研究生成式 AI 风险")
    assert explicit and explicit.mode == "quick" and explicit.signals == ("explicit_mode",)

    greeting = decide("auto", "你好！")
    assert greeting and greeting.mode == "quick" and greeting.signals == ("greeting",)

    help_request = decide("auto", "你有什么功能？")
    assert help_request and help_request.mode == "chat" and help_request.signals == ("help",)

    preference = decide("auto", "当前用户的研究偏好是什么？")
    assert preference and preference.mode == "chat" and preference.signals == ("preference",)

    research = decide("auto", "请做一份生成式 AI 行业趋势与竞争研究")
    assert research and research.mode == "deep" and {"行业", "趋势", "竞争", "研究"} <= set(research.signals)

    industry = decide("auto", "AI 行业研究偏好有哪些？")
    assert industry and industry.mode == "deep"
    name_set = decide("auto", "以后叫你小研")
    assert name_set and name_set.signals == ("assistant_name_set",)
    name_query = decide("auto", "你叫什么名字？")
    assert name_query and name_query.signals == ("assistant_name_query",)
    assert assistant_name_setting("你叫什么名字？") is None


def test_ambiguous_request_is_left_to_llm():
    assert decide("auto", "帮我看看这个问题") is None
