"""Explicit deterministic fixtures, isolated from live provider mode."""
import hashlib
import math


def embed(text):
    vector = [0.0] * 64
    for char in text:
        vector[int(hashlib.sha256(char.encode()).hexdigest()[:4], 16) % 64] += 1
    length = math.sqrt(sum(value * value for value in vector)) or 1
    return [value / length for value in vector]


def search(query):
    return [
        {"url": "https://example.com/research-fixture/workflow", "name": "测试资料：研究工作流",
         "summary": "本资料仅用于功能测试。研究助手先拆解问题，再收集网络和本地资料，随后核查证据与引用。研究报告需要说明证据不足及统计口径冲突，不应编造数据。"},
        {"url": "https://example.com/research-fixture/memory", "name": "测试资料：记忆与恢复",
         "summary": "本资料仅用于功能测试。会话记忆保存当前研究上下文，用户偏好单独保存。历史报告摘要只能帮助定位过去的研究，新的事实判断需要重新查阅原始证据。"},
    ]


def generate(role, data):
    if role == "router":
        return {"mode": "deep", "reason": "测试模式：展示完整研究流程"}
    if role == "chat":
        return {"answer": "我是 DeepResearch，可以帮助你组织带来源的行业研究；需要外部事实时，请告诉我研究主题、地区和时间范围。"}
    if role == "planner":
        return {"title": "研究工作流演示", "questions": ["如何组织研究流程？", "如何管理研究记忆？"],
                "queries": [data["topic"][:200]], "scope": "固定测试资料，不提供真实行业结论"}
    if role == "judge":
        return {"accepted_ids": [row["id"] for row in data["evidence"]], "conflicts": [], "notes": []}
    if role == "analyst":
        return {"claims": [{"text": row["text"][:240], "source_ids": [row["id"]]} for row in data["evidence"][:5]],
                "gaps": ["测试模式补搜场景"]}
    if role == "reflect":
        return {"queries": ["补充测试资料查询"] if "补充测试资料查询" not in data["searched"] else [],
                "reason": "测试补搜与停止条件"}
    if role == "writer":
        return {"title": "研究工作流演示报告", "sections": [{"heading": "证据整理", "claims": data["claims"]}],
                "limitations": ["模拟模型输出，仅供功能验收"]}
    if role == "validator":
        return {"checks": [{"index": c["index"], "supported": c["text"] in " ".join(s["text"] for s in c["sources"]),
                            "reason": "固定数据逐字匹配检查"} for c in data["claims"]]}
    if role == "repair":
        return data["draft"]
    raise ValueError(f"Unknown demo role: {role}")
