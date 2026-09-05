"""Deterministic routing policy used before the LLM fallback."""
from dataclasses import dataclass
import re


GREETING = re.compile(r"(?i)(你好|您好|hi|hello|谢谢|感谢)[！!。,.，\s]*")
HELP = re.compile(
    r"(?i)(?:请问)?(?:你|您|这个(?:系统|项目|助手)?).{0,12}"
    r"(?:有什么功能|能做什么|可以做什么|支持什么|怎么使用|怎么用|如何使用)[？?！!。,.，\s]*"
)
HELP_SHORT = re.compile(r"(?i)(?:功能介绍|使用说明|帮助)[？?！!。,.，\s]*")
PREFERENCE_QUERY = re.compile(
    r"(?i)(?:(?:当前(?:用户)?|我的|我(?:的)?|用户(?:的)?).{0,12}(?:研究)?偏好.*"
    r"|你.{0,12}(?:记住|保存).{0,12}(?:研究)?偏好.*)"
)
ASSISTANT_NAME_SET = re.compile(
    r"^\s*(?:(?:以后|从现在起|今后)(?:请)?叫(?:你|您|助手|DeepResearch)?|"
    r"(?:请|麻烦)?(?:你|您|助手|DeepResearch)(?:的名字)?(?:叫|是|改叫|改为|改成)|"
    r"(?:给|帮)(?:你|助手|DeepResearch)(?:起名(?:字)?(?:叫)?|取名(?:叫)?))\s*"
    r"[“\"'《]?(?P<name>[\u4e00-\u9fffA-Za-z0-9_-]{1,24})[”\"'》]?[！!。,.，\s]*$",
    re.IGNORECASE,
)
ASSISTANT_NAME_QUERY = re.compile(
    r"^\s*(?:(?:你|您|助手|DeepResearch)(?:的)?名字(?:是|叫)?(?:什么|啥|谁)?|"
    r"(?:你|您|助手|DeepResearch)叫(?:什么|啥|谁)(?:名字)?)\s*[？?！!。,.，\s]*$",
    re.IGNORECASE,
)
DEEP_SIGNALS = {
    "调研", "研究", "行业", "对比", "比较", "趋势", "报告", "市场",
    "竞争", "政策", "监管", "风险", "机会", "商业模式", "融资",
}


@dataclass(frozen=True)
class RuleDecision:
    mode: str
    reason: str
    signals: tuple[str, ...]


def decide(requested_mode: str, topic: str) -> RuleDecision | None:
    """Return a transparent rule decision, otherwise leave judgment to the LLM.

    The policy intentionally handles only high-confidence cases.  Ambiguous
    requests remain an LLM task instead of being forced through a brittle
    keyword classifier.
    """
    text = topic.strip()
    if requested_mode in {"quick", "deep"}:
        return RuleDecision(requested_mode, "按用户指定模式执行", ("explicit_mode",))
    if assistant_name_setting(text):
        return RuleDecision("chat", "保存当前用户设置的助手名称", ("assistant_name_set",))
    if ASSISTANT_NAME_QUERY.fullmatch(text):
        return RuleDecision("chat", "查询当前用户设置的助手名称", ("assistant_name_query",))
    if GREETING.fullmatch(text):
        return RuleDecision("quick", "礼貌问候无需检索", ("greeting",))
    if PREFERENCE_QUERY.fullmatch(text):
        return RuleDecision("chat", "查询当前用户保存的研究偏好", ("preference",))
    matches = tuple(word for word in sorted(DEEP_SIGNALS) if word in text)
    if matches:
        return RuleDecision("deep", "命中多维度研究信号", matches)
    if HELP.fullmatch(text) or HELP_SHORT.fullmatch(text):
        return RuleDecision("chat", "产品功能与使用帮助无需检索", ("help",))
    return None


def assistant_name_setting(topic: str) -> str | None:
    """Extract an explicit assistant-name instruction without guessing from ordinary chat."""
    if "?" in topic or "？" in topic:
        return None
    match = ASSISTANT_NAME_SET.fullmatch(topic.strip())
    if not match:
        return None
    name = match.group("name")
    return None if name in {"什么", "什么名字", "名字", "谁", "啥"} else name
