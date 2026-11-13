"""Deterministic evidence gates that run before model-assisted validation."""
import re

NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])(?P<value>(?:\d{1,3}(?:[,，]\s*\d{3})+|\d+)(?:\.\d+)?)"
    r"(?:\s*(?P<unit>[%％]|万|亿|年|月|日|美元|元|条|项|家|倍))?"
)


def numeric_tokens(text):
    # Normalize formatting only: never convert units, ratios, scales or currencies.
    return {(re.sub(r"[,，\s]", "", match['value']), (match['unit'] or '').replace('％','%'))
            for match in NUMBER.finditer(text)}
HIGH_STAKES = re.compile(r"\d|法规|法律|条例|监管|罚款|比例|百分比|金额|市场规模|增长率")
ENUMERATED_SCOPE = re.compile(
    r"(?:结论|事实|结果|发现|原因|问题|事项)(?:性表述)?(?:仅有|只有|仅为|总共|一共|共计|为)"
    r"[一二三四五六七八九十百两\d]+(?:条|项|个)"
)
TERM_RELATION_META = re.compile(
    r"(?:术语|用词).{0,100}(?:对应关系|等价关系|是否等价).{0,40}"
    r"(?:尚未确认|未确认|无法确认|不能确认|尚未证实|无法判断)"
)
UNSTATED_RELATION_META = re.compile(
    r"(?:资料|原文|文档).{0,12}(?:未说明|未明确|没有说明).{0,100}"
    r"(?:之间|关系|还是|是否.{0,24}(?:同一|相同)(?:批次|范围|对象))"
)
MISSING_COUNT_UNIT = re.compile(
    r"(?:均|都)?.{0,12}(?:未明确|未提供|未给出|没有说明|并未说明).{0,8}(?:计数)?单位"
    # Match the unit field, not a metric whose name begins with 单位 (unit time/cost).
    r"(?=$|[^\u4e00-\u9fffA-Za-z0-9_]|(?:为|是|的|信息|名称|说明|定义|符号|口径|是否|一致|等价|相同|不同))"
)
EXPLICIT_COUNT_UNIT = re.compile(
    r"(?<![A-Za-z])\d+(?:[,.]\d+)?\s*(?:名|人|个|条|项|家|组|批|份|台|辆|次|件|套|位)"
)
SHARED_BATCH_UNCERTAINTY = re.compile(
    r"(?:是否.{0,24}(?:同一|相同)批次.{0,64}(?:无法|不能|未能).{0,20}(?:判定|确认|判断)"
    r"|(?:无法|不能|未能).{0,64}(?:判定|确认|判断).{0,24}是否.{0,24}(?:同一|相同)批次)"
)
UNRESOLVED_SUPERSESSION = re.compile(r"(?:能否|是否|可否).{0,16}(?:替代|取代).{0,40}(?:还需|需要|无法|不能|未能)")
MISSING_STATUS_CODE = re.compile(r"(?:未|没有|并未|尚未)(?:给出|提供|说明|列出|展示|注明|写明)[^，。；;]{0,20}(?:具体)?(?:HTTP)?(?:错误)?状态码")
EXPLICIT_STATUS_CODE = re.compile(
    r"(?:HTTP(?:status(?:code)?)?[`*:=：-]{0,8}|(?:HTTP)?(?:错误)?状态码(?:为|是)?[`*:=：-]{0,8})[1-5]\d{2}(?!\d)", re.I
)
ATTRIBUTED_CONFLICT = re.compile(r"(?:均|都|各自)(?:明确)?(?:指出|记载|声明|说明|提到).{0,60}(?:冲突|矛盾|不一致)")
LOCAL_CLAUSE_START = r"(?:^|[，,。；;：:？！?!\r\n])\s*"
LOCAL_VERSION = r"v\d+(?:\.\d+)*"
LOCAL_DOCUMENT_SUBJECT = (
    r"(?:所提供的)?(?:[一二三四五六七八九十两\d]+份)?(?:合成演示)?资料"
    rf"(?:《[^《》\r\n]+》)?(?:{LOCAL_VERSION})?(?:评审纪要|补充试验|评审|试验)?"
    r"(?:[（(]记录日期\d{4}-\d{2}-\d{2}[）)])?"
)
LOCAL_RECORD_ATTRIBUTION = re.compile(
    LOCAL_CLAUSE_START + rf"(?P<subject>{LOCAL_DOCUMENT_SUBJECT})"
    r"(?:均|都|各自)?(?:自注|标明|标注|记载|记录|说明|规定|指出|提到)"
)
LOCAL_SCOPE_ATTRIBUTION = re.compile(
    LOCAL_CLAUSE_START + r"(?:在|仅就|就)(?P<subject>" + LOCAL_DOCUMENT_SUBJECT + r")"
    r"(?:中|(?:的|所设定的)?(?:同次)?(?:记录|样本|测试|试验|需求与试验情境)(?:中|内|而言))"
)
LOCAL_VERSIONED_ATTRIBUTION = re.compile(
    LOCAL_CLAUSE_START + r"(?:(?:根据|依据|对照)所提供的)?"
    r"(?P<subject>(?:需求|评审纪要|评审|补充试验|试验|待验清单|清单)"
    + LOCAL_VERSION + r")(?:记载|记录|说明|规定|指出|提到|列出|将)"
)
LOCAL_LEGACY_ATTRIBUTION = re.compile(
    LOCAL_CLAUSE_START + r"(?P<subject>(?:(?:根据|仅?依据)[^《》，,。；;：:？！?!\r\n]*资料"
    r"(?:《[^《》\r\n]+》)?|资料中|文档中|该文档))"
)


def local_locator_pattern(locator: str) -> str:
    literal = r"\s*".join(re.escape(c) for c in locator if not c.isspace())
    fields = re.fullmatch(r"(.*?)\s*·\s*字符\s*(\d+)\s*[–-]\s*(\d+)", locator)
    if fields is None:
        return literal
    heading, start, end = fields.groups()
    heading = r"\s*".join(re.escape(c) for c in heading if not c.isspace())
    start = r"\s*".join(re.escape(c) for c in start)
    end = r"\s*".join(re.escape(c) for c in end)
    span = rf"字\s*符\s*{start}\s*[–-]\s*{end}"
    # These delimiters format the same bound heading and complete character range.
    # They do not normalize numbers, units, or any part of the factual claim.
    return heading + rf"\s*(?:·\s*{span}|（\s*{span}\s*）|\(\s*{span}\s*\))"


def has_local_attribution(text: str, sources: list[dict]) -> bool:
    compact = re.sub(r"\s+", "", text)
    labels = [re.sub(r"\s+", "", source.get(field, ""))
              for source in sources for field in ("title", "locator")]
    # A short book-title label may be the heading in a real chunk locator.
    labels = [re.split(r"·字符\d", label, maxsplit=1)[0] for label in labels]
    for pattern in (LOCAL_RECORD_ATTRIBUTION, LOCAL_SCOPE_ATTRIBUTION, LOCAL_LEGACY_ATTRIBUTION):
        for match in pattern.finditer(compact):
            quoted = re.findall(r"《([^《》]+)》", match["subject"])
            if all(any(label == title or label.endswith("：" + title) for label in labels) for title in quoted):
                return True
    # A versioned document descriptor is a source-bound name, not an arbitrary
    # requirement mentioned in the question or a promise of external verification.
    for match in LOCAL_VERSIONED_ATTRIBUTION.finditer(compact):
        if any(match["subject"] in re.sub(r"\s+", "", source.get(field, ""))
               for source in sources for field in ("title", "locator", "text")):
            return True
    return bound_local_attribution(compact, sources)


def local_attribution_clauses(text: str, separators: str) -> list[str]:
    pairs = {"《": "》", "（": "）", "(": ")", "“": "”"}
    stack, clauses, start = [], [], 0
    for index, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return []
        elif char in separators and not stack:
            clauses.append(text[start:index])
            start = index + 1
    return [] if stack else [*clauses, text[start:]]


def bound_local_attribution(text: str, sources: list[dict]) -> bool:
    """Recognize affirmative document scope; content still needs full semantic checks."""
    if not sources or any(source.get("kind") != "local" for source in sources) or re.search(r"[？?]", text):
        return False
    documents = []
    for source in sources:
        body = re.sub(r"\s+", "", source.get("text", ""))
        heading = re.split(r"\s*·\s*字符\s*\d", source.get("locator", ""), maxsplit=1)[0]
        labels = {re.sub(r"\s+", "", source.get("title", "")), re.sub(r"\s+", "", heading)} - {""}
        labels |= {re.split(r"[：:]", label)[-1] for label in labels}
        # Only explicit metadata declarations supply additional document names.
        # A version mentioned in a comparison in the body is not this document's version.
        declarations = re.findall(r"(?:^|[。；;\n])\s*(?:文档|试验|记录)?版本\s*[:：]\s*([^。；;\n]+)", source.get("text", ""))
        declarations = {re.sub(r"\s+", "", value) for value in declarations}
        labels |= {value for value in declarations if not re.fullmatch(LOCAL_VERSION, value)}
        versions = {version for label in labels | declarations for version in re.findall(LOCAL_VERSION, label)}
        dates = set(re.findall(r"(?:^|[。；;\n])\s*记录日期\s*[:：]\s*(\d{4}-\d{2}-\d{2})", source.get("text", "")))
        documents.append((labels, versions, dates, body))
    nouns = re.compile(r"资料|材料|文档|文件|需求|评审|纪要|试验|测试|清单|记录|结果|样本|项目|情境|说明|范围")

    def bound_subject(subject):
        provided = bool(re.match(r"(?:所提供的?|这些|上述|该)", subject))
        subject = re.sub(r"^(?:所提供的?|这些|上述|该)", "", subject)
        parts = local_attribution_clauses(subject, "、和及与")
        if not parts:
            return False
        for part in parts:
            part = re.sub(r"^(?:[一二三四五六七八九十两\d]+(?:份|次|个))", "", part)
            quoted = re.findall(r"《([^《》]+)》", part)
            if part.count("《") != len(quoted) or part.count("》") != len(quoted):
                return False
            unquoted = re.sub(r"《[^《》]+》", "", part)
            if re.search(r"并非|不是|没有|未|不曾|是否|能否|可否", unquoted):
                return False
            versions = set(re.findall(LOCAL_VERSION, part))
            dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", part))
            candidates = [(labels, body) for labels, source_versions, source_dates, body in documents
                          if all(title in labels for title in quoted)
                          and versions <= source_versions and dates <= source_dates]
            if not candidates:
                return False
            name = unquoted
            name = re.sub(r"\d{4}-\d{2}-\d{2}|" + LOCAL_VERSION, "", name)
            name = re.sub(r"记录日期|[（()），,）]", "", name)
            name = re.sub(r"所设定的|所描述的|所记录的|中的|的|中", "", name)
            scoped_name = re.sub(r"合成演示|虚构", "", name)
            if not any(
                name in labels or (quoted and not name) or (
                    (provided or quoted or "合成演示资料" in part or "虚构" in part
                     or any(name and name in label for label in labels))
                    and (matches := list(nouns.finditer(scoped_name))) and matches[-1].end() == len(scoped_name)
                    and all(
                        not fragment or fragment in body or fragment in "".join(labels)
                        for fragment in nouns.split(scoped_name)
                    ) and all(noun.group() in body or noun.group() in "".join(labels)
                              for noun in nouns.finditer(name))
                    and ("合成演示" not in part or "合成演示资料" in body)
                    and ("虚构" not in part or "虚构" in body)
                ) for labels, body in candidates
            ):
                return False
        return True

    for clause in local_attribution_clauses(text, "，,。；;：:\r\n"):
        if re.match(r"(?:并非|不是|没有|未|不|是否|能否)", clause):
            continue
        according = re.match(r"(?:根据|依据|对照|按照|按)(.+)$", clause)
        if according and bound_subject(according[1]):
            return True
        scope = re.match(r"(?:仅针对|针对|仅就|就|在)(.+?)(?:范围内|而言|判断|中|内)?$", clause)
        if scope and bound_subject(scope[1]):
            return True
        # Direct document subjects need a record predicate or explicit containment.
        for predicate in re.finditer(r"(?:均|都|各自)?(?:明确)?(?:记载|记录|说明|规定|指出|提到|列出|将|称|中)", clause):
            subject = clause[:predicate.start()]
            if predicate.group().endswith("中") and not re.search(
                r"(?:资料|材料|文档|文件|记录|试验|样本|情境|项目|》|" + LOCAL_VERSION + r")$", subject
            ):
                continue
            if not re.search(r"(?:未|没有|并非|不是|不曾|不)(?:明确)?$", subject) and bound_subject(subject):
                return True
        classification = re.search(r"(?:均|都|各自)?(?:明确)?(?:自标为|自注为|标明为|标注为|属于)[“\"]?合成演示资料", clause)
        if (classification and all("合成演示资料" in body for _, _, _, body in documents)
                and bound_subject(clause[:classification.start()])):
            return True
    return False


def requires_strong_evidence(text: str) -> bool:
    return bool(HIGH_STAKES.search(text))


def validate_claim(text: str, sources: list[dict]) -> tuple[bool, str]:
    """Require traceable, independent support for numeric or regulatory claims."""
    combined = "\n".join(source.get("text", "") for source in sources)
    # A selected finding does not establish an exhaustive count of all findings.
    # Keep this conservative lexical gate separate from semantic validation.
    compact = re.sub(r"\s+", "", text)
    source_text = re.sub(r"\s+", "", combined)
    if ATTRIBUTED_CONFLICT.search(compact) and not all(
        re.search(r"冲突|矛盾|不一致|不同|差异", source.get("text", "")) for source in sources
    ):
        return False, "跨来源对照得出的差异不能冒充每份来源自行声明的冲突；请直接报告各自记录或明确标注推断"
    term_meta = TERM_RELATION_META.search(compact)
    if term_meta and term_meta.group() not in source_text:
        return False, "术语对应关系是分析边界，引用原文未陈述时应放入局限或缺口，不能作为带引用的事实结论"
    relation_meta = UNSTATED_RELATION_META.search(compact)
    if relation_meta and relation_meta.group() not in source_text:
        return False, "主体关系缺失是分析边界；原文未明确陈述时应放入局限或缺口，不能作为带引用的事实结论"
    if SHARED_BATCH_UNCERTAINTY.search(compact) and sources and all(
        re.search(r"(?:同一|相同)批次", re.sub(r"\s+", "", source.get("text", "")))
        for source in sources
    ):
        return False, "引用原文均已明确同一批次，不能因缺少批次编号而反向声称是否同批次无法判定"
    if UNRESOLVED_SUPERSESSION.search(compact) and re.search(r"旧版.{0,24}(?:已被|已经被).{0,12}(?:替代|取代)", source_text):
        return False, "引用原文已明确旧版被替代，不能把版本替代关系写成尚待确认"
    missing_status = MISSING_STATUS_CODE.search(compact)
    if missing_status and missing_status.group() not in source_text and EXPLICIT_STATUS_CODE.search(source_text):
        return False, "引用原文已给出明确HTTP状态码，不能声称具体状态码未提供；请保留状态码并把其他缺口分开说明"
    if MISSING_COUNT_UNIT.search(compact) and EXPLICIT_COUNT_UNIT.search(combined):
        return False, "引用原文已给出明确计数单位，不能写成单位未说明；如不同来源单位不一致，应直接保留单位差异"
    if any(match.group() not in source_text for match in ENUMERATED_SCOPE.finditer(compact)):
        return False, "原文未明确列举结论或事项总数；请直接陈述事实，保留并列限制，不自行断言仅有几条"
    # Source IDs are provenance metadata rendered separately from claim text.
    # A model may still repeat one in prose; its hexadecimal digits are not
    # factual numbers and must not make an otherwise supported claim fail.
    numeric_text = text
    named_local_attribution = False
    local_titles = set()
    for source in sources:
        if source.get("id"):
            numeric_text = numeric_text.replace(source["id"], "")
        if source.get("kind") == "local" and source.get("title"):
            title = re.escape(f"《{source['title']}》")
            local_titles.add(title)
            label = title
            if source.get("locator"):
                # Only the cited source's exact locator is metadata. Formatting
                # whitespace may differ, but invented ranges remain factual targets.
                locator = local_locator_pattern(source["locator"])
                located_label = title + rf"\s*[（(]\s*{locator}\s*[）)]"
                numeric_text = re.sub(located_label, "", numeric_text)
                # The same bound label may be written as an explicit citation
                # field rather than parenthesized after a book-title label.
                citation_field = (r"引用(?:位置)?\s*[:：]\s*" + re.escape(source["title"])
                                  + rf"\s*[,，]\s*{locator}(?=$|[。；;，,：:\n])")
                numeric_text = re.sub(citation_field, "", numeric_text)
                label += rf"(?:\s*[（(]\s*{locator}\s*[）)])?"
            named_local_attribution |= bool(re.search(
                LOCAL_CLAUSE_START + label + r"\s*(?:正文)?\s*(?:明确)?(?:记载|说明|规定|记录|指出|提到)", text
            ))
    # The same document may supply several chunks. Consume every bound locator
    # before removing bare titles so source ordering cannot hide a later locator.
    for title in local_titles:
        numeric_text = re.sub(title, "", numeric_text)
    numbers = numeric_tokens(numeric_text)
    normalized = numeric_tokens(combined)
    source_values = {value for value, _ in normalized}
    if any((value, unit) not in normalized if unit else value not in source_values for value, unit in numbers):
        return False, "数字、日期或比例未在引用片段中逐字出现"
    if sources and all(s.get("kind") == "attachment" for s in sources):
        if not re.search(r"图中|图片|图表|截图", text):
            return False, "图片结论必须明确限定为图中显示，不能视作外部核实事实"
        return True, "图片可见内容通过数字检查，仍需语义校验"
    if sources and all(s.get("kind") == "local" for s in sources):
        if not named_local_attribution and not has_local_attribution(text, sources):
            return False, "内部资料结论必须明确限定为资料中的记录，不能当作外部核实事实"
        return True, "资料内陈述通过数字检查，仍需版本和语义校验"
    if not requires_strong_evidence(text):
        return True, "常规结论通过确定性引用检查"
    strong = [s for s in sources if s.get("access") in {"fulltext", "document"}]
    trusted = any(s.get("trust_label") for s in sources)
    domains = {s.get("domain") for s in strong if s.get("domain")}
    if not strong:
        return False, "重要结论不能仅由搜索摘要支撑"
    if not trusted and len(domains) < 2:
        return False, "重要结论需要两个独立网页域名或一个已配置可信域名"
    return True, "重要结论满足正文与独立来源门控"
