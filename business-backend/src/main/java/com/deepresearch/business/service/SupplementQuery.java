package com.deepresearch.business.service;

import java.util.regex.Pattern;

import com.deepresearch.business.api.Requests;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

final class SupplementQuery {
    private static final int MAX_LENGTH = 1000;
    private static final Pattern CONFIRMED_OBJECT = Pattern.compile("；研究对象=([^；]+)；交付标准=");
    private static final Pattern ENGLISH_COMPARISON = Pattern.compile(
        "(?i)\\bcompare\\s+([^，,。;；?？\\n]{1,150}?\\s+(?:with|and|versus|vs\\.?)\\s+[^，,。;；?？\\n]{1,150})");
    private static final Pattern COMPARISON_SUFFIX = Pattern.compile(
        "\\s*(?:两条路线|两种方案|两个方案|各自|之间的|分别|在(?:项目|性能|成本|检索|使用|部署)|的(?:能力|差异|区别|优劣)).*$");

    private SupplementQuery() {}

    static String suggest(String originalTopic, String target) {
        String focus = whitespace(target);
        var confirmed = CONFIRMED_OBJECT.matcher(originalTopic);
        String objects = confirmed.find() ? confirmed.group(1).strip() : "";
        if (objects.isEmpty() || "不限定".equals(objects)) {
            var request = new Requests.Run();
            request.setTopic(originalTopic);
            objects = ResearchIntakePolicy.scopeSuggestions(request).get("object");
            if (objects.isBlank()) {
                var english = ENGLISH_COMPARISON.matcher(originalTopic);
                if (english.find()) objects = english.group(1);
            }
            objects = COMPARISON_SUFFIX.matcher(objects).replaceFirst("").strip();
        }
        String query = objects.isEmpty() ? focus : whitespace(objects + " " + focus);
        return query.length() <= MAX_LENGTH ? query : query.substring(0,
            Character.isHighSurrogate(query.charAt(MAX_LENGTH - 1)) ? MAX_LENGTH - 1 : MAX_LENGTH).strip();
    }

    static String edited(String value) {
        if (value == null) return null;
        String query = whitespace(value);
        if (query.isEmpty() || query.length() > MAX_LENGTH)
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "检索词须为 1 至 1000 个字符");
        return query;
    }

    private static String whitespace(String value) {
        return value.replaceAll("(?U)\\s+", " ").strip();
    }
}
