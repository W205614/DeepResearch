"""Deterministic evidence gates that run before model-assisted validation."""
import re

NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[,.]\d+)?(?:\s*[%％]|\s*(?:万|亿|年|月|日|美元|元|条|项|家|倍))?")
HIGH_STAKES = re.compile(r"\d|法规|法律|条例|监管|罚款|比例|百分比|金额|市场规模|增长率")


def requires_strong_evidence(text: str) -> bool:
    return bool(HIGH_STAKES.search(text))


def validate_claim(text: str, sources: list[dict]) -> tuple[bool, str]:
    """Require traceable, independent support for numeric or regulatory claims."""
    combined = "\n".join(source.get("text", "") for source in sources)
    numbers = [token.replace("％", "%").replace(",", "") for token in NUMBER.findall(text)]
    normalized = combined.replace("％", "%").replace(",", "")
    if any(token not in normalized for token in numbers):
        return False, "数字、日期或比例未在引用片段中逐字出现"
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
