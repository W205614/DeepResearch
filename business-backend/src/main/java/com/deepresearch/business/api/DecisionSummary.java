package com.deepresearch.business.api;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

final class DecisionSummary {
    private static final int MAX_KEY_CLAIMS = 10;

    private DecisionSummary() {}

    static String markdown(Map<String, Object> report, boolean publication) {
        Map<?, ?> validation = (Map<?, ?>) report.get("validation");
        List<?> claims = list(validation.get("claim_links"));
        List<?> requirements = list(validation.get("requirements"));
        StringBuilder out = new StringBuilder("# 研究决策摘要\n\n");
        out.append("原问题：").append(text(report.get("topic"))).append("\n\n");
        out.append("版本：").append(publication ? "正式报告记录" : "研究草稿");
        if (publication && report.get("revision_no") != null)
            out.append(" · 第 ").append(report.get("revision_no")).append(" 版");
        out.append(" · ").append(text(report.get("id"))).append("\n");
        if (publication) {
            out.append("状态：").append(switch (String.valueOf(report.get("status"))) {
                case "published" -> "已发布"; case "pending" -> "待审核";
                case "withdrawn" -> "已撤回"; case "rejected" -> "已驳回";
                default -> "未记录";
            }).append("\n");
            if (report.get("review_reason") instanceof String note && !note.isBlank())
                out.append("审核说明：").append(text(note)).append("\n");
        }
        out.append("\n## 关键结论\n\n");
        Map<Object, Map<?, ?>> byId = new LinkedHashMap<>();
        for (Object value : claims) if (value instanceof Map<?, ?> row) byId.put(row.get("id"), row);

        Set<Object> selected = new LinkedHashSet<>();
        for (Object value : requirements) {
            if (value instanceof Map<?, ?> row)
                list(row.get("claim_ids")).stream().filter(byId::containsKey).findFirst().ifPresent(selected::add);
        }
        selected.addAll(byId.keySet());
        Set<Object> sources = new LinkedHashSet<>();
        for (Object key : selected.stream().limit(MAX_KEY_CLAIMS).toList()) {
            Map<?, ?> claim = byId.get(key);
            List<?> references = list(claim.get("source_ids"));
            out.append("- ").append(text(claim.get("text")));
            for (Object reference : references) out.append(" [").append(text(reference)).append("]");
            out.append("\n");
            sources.addAll(references);
        }
        if (selected.isEmpty()) out.append("此报告没有可导出的结构化核验结论。请查看完整报告。\n");
        if (selected.size() > MAX_KEY_CLAIMS) out.append("\n其余结论见完整报告。\n");

        out.append("\n## 适用条件与局限\n\n");
        out.append("以原问题约定、各条结论的限定条件及所列引用版本为准。\n");
        List<Object> limitations = new ArrayList<>();
        limitations.addAll(list(validation.get("gaps")));
        limitations.addAll(list(validation.get("limitations")));
        for (Object item : new LinkedHashSet<>(limitations))
            out.append("- ").append(text(item)).append("\n");

        out.append("\n## 未答项\n\n");
        boolean unresolved = false;
        for (Object value : requirements) {
            if (value instanceof Map<?, ?> row && !"answered".equals(row.get("status"))) {
                out.append("- ").append(text(row.get("text"))).append("：").append(text(row.get("gap"))).append("\n");
                unresolved = true;
            }
        }
        if (requirements.isEmpty()) {
            for (Object item : list(validation.get("unanswered_questions"))) {
                out.append("- ").append(text(item)).append("\n");
                unresolved = true;
            }
        }
        if (!unresolved) out.append(requirements.isEmpty() ? "历史报告未记录逐项覆盖。\n" : "无已记录的未答项。\n");

        out.append("\n## 引用定位\n\n");
        for (Object value : list(report.get("sources"))) {
            if (!(value instanceof Map<?, ?> source) || !sources.contains(source.get("id"))) continue;
            out.append("- [").append(text(source.get("id"))).append("] ").append(text(source.get("title")));
            if (source.get("index_version") instanceof Number version && version.intValue() > 0)
                out.append(" · 资料版本 ").append(version);
            for (String field : List.of("locator", "url", "retrieved_at"))
                if (source.get(field) instanceof String detail && !detail.isBlank()) out.append(" · ").append(text(detail));
            out.append("\n");
        }
        return out.toString();
    }

    private static List<?> list(Object value) {
        return value instanceof List<?> rows ? rows : List.of();
    }

    private static String text(Object value) {
        String raw = value == null ? "" : value.toString().replaceAll("\\s+", " ").strip();
        StringBuilder escaped = new StringBuilder();
        for (char character : raw.toCharArray()) {
            if (character == '\\' || "`*_{}[]()#+-.!<>|".indexOf(character) >= 0) escaped.append('\\');
            escaped.append(character);
        }
        return escaped.toString();
    }
}
