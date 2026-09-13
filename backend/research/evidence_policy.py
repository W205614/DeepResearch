"""Deterministic evidence gates that run before model-assisted validation."""
import re

NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[,.]\d+)?(?:\s*[%％]|\s*(?:万|亿|年|月|日|美元|元|条|项|家|倍))?")
HIGH_STAKES = re.compile(r"\d|法规|法律|条例|监管|罚款|比例|百分比|金额|市场规模|增长率")
ENUMERATED_SCOPE = re.compile(
    r"(?:结论|事实|结果|发现|原因|问题|事项)(?:性表述)?(?:仅有|只有|仅为|总共|一共|共计|为)"
    r"[一二三四五六七八九十百两\d]+(?:条|项|个)"
)


def requires_strong_evidence(text: str) -> bool:
    return bool(HIGH_STAKES.search(text))


def validate_claim(text: str, sources: list[dict]) -> tuple[bool, str]:
    """Require traceable, independent support for numeric or regulatory claims."""
    combined = "\n".join(source.get("text", "") for source in sources)
    # A selected finding does not establish an exhaustive count of all findings.
    # Keep this conservative lexical gate separate from semantic validation.
    compact = re.sub(r"\s+", "", text)
    source_text = re.sub(r"\s+", "", combined)
    if any(match.group() not in source_text for match in ENUMERATED_SCOPE.finditer(compact)):
        return False, "原文未明确列举结论或事项总数；请直接陈述事实，保留并列限制，不自行断言仅有几条"
    numbers = [token.replace("％", "%").replace(",", "") for token in NUMBER.findall(text)]
    normalized = combined.replace("％", "%").replace(",", "")
    if any(token not in normalized for token in numbers):
        return False, "数字、日期或比例未在引用片段中逐字出现"
    if sources and all(s.get("kind") == "attachment" for s in sources):
        if not re.search(r"图中|图片|图表|截图", text):
            return False, "图片结论必须明确限定为图中显示，不能视作外部核实事实"
        return True, "图片可见内容通过数字检查，仍需语义校验"
    if sources and all(s.get("kind") == "local" for s in sources):
        if not re.search(r"根据.{0,8}资料|资料中|文档中|该文档", text):
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
