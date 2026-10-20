package com.deepresearch.business.service;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

public final class PageCursor {
    private PageCursor() {}
    public static String[] decode(String cursor, int limit) {
        if (limit < 1 || limit > 100) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "每页数量须为 1 到 100");
        if (cursor == null || cursor.isBlank()) return new String[]{"", ""};
        try {
            String[] keys = new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8).split("\n", -1);
            if (keys.length != 2 || keys[0].length() > 80 || !keys[1].matches("[a-zA-Z0-9_-]{1,80}"))
                throw new IllegalArgumentException();
            return keys;
        } catch (IllegalArgumentException error) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "分页游标无效"); }
    }
    public static String encode(Map<String, Object> row) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(
            (row.get("created_at") + "\n" + row.get("id")).getBytes(StandardCharsets.UTF_8));
    }
}
