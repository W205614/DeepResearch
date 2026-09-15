package com.deepresearch.business.service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.config.DeepResearchProperties;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
public class RunViewService {
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {};
    private static final TypeReference<List<Map<String, Object>>> LIST = new TypeReference<>() {};
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final DeepResearchProperties properties;

    public RunViewService(JdbcTemplate jdbc, ObjectMapper mapper, DeepResearchProperties properties) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.properties = properties;
    }

    public Map<String, Object> ownedRun(String runId, String workspaceId) {
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT * FROM runs WHERE id=? AND user_id=?", runId, workspaceId);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在");
        return render(rows.getFirst());
    }

    public List<Map<String, Object>> threadRuns(String threadId, String workspaceId) {
        return jdbc.queryForList("SELECT * FROM runs WHERE thread_id=? AND user_id=? ORDER BY created_at", threadId, workspaceId)
            .stream().map(this::render).toList();
    }

    public Map<String, Object> render(Map<String, Object> source) {
        Map<String, Object> row = new LinkedHashMap<>(source);
        String runId = String.valueOf(row.get("id"));
        row.put("sources", jsonList(row.get("sources")));
        Map<String, Object> validation = jsonMap(row.get("validation"));
        validation.putIfAbsent("quality", "unknown");
        row.put("validation", validation);
        Map<String, Object> errorInfo = jsonMap(row.get("error_info"));
        row.put("error_info", errorInfo);
        row.put("attachments", jdbc.queryForList("""
            SELECT id,name,media_type,size,position FROM attachments
            WHERE run_id=? AND status='ready' ORDER BY position""", runId));
        List<Map<String, Object>> usage = jdbc.queryForList("SELECT * FROM counters WHERE run_id=?", runId);
        row.put("usage", usage.isEmpty() ? Map.of() : usage.getFirst());
        long deadline = number(row.get("deadline_at"));
        long calls = number(row.get("call_attempts"));
        long reserved = number(row.get("reserved_tokens"));
        String status = String.valueOf(row.get("status"));
        boolean exhausted = Boolean.FALSE.equals(errorInfo.get("retryable")) || (deadline > 0 && deadline <= Instant.now().getEpochSecond());
        boolean canResume = List.of("failed", "interrupted", "cancelled").contains(status) && !exhausted
            && calls < properties.getMaxRunCallAttempts() && reserved < properties.getMaxRunReservedTokens();
        row.put("can_resume", canResume);
        row.put("recovery", Map.of(
            "auto_recoveries", number(row.get("auto_recoveries")),
            "deadline_at", deadline,
            "call_attempts", calls,
            "reserved_tokens", reserved,
            "remaining_calls", Math.max(0, properties.getMaxRunCallAttempts() - calls),
            "remaining_reserved_tokens", Math.max(0, properties.getMaxRunReservedTokens() - reserved)));
        return row;
    }

    private Map<String, Object> jsonMap(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> copy = new LinkedHashMap<>();
            map.forEach((key, item) -> copy.put(String.valueOf(key), item));
            return copy;
        }
        try {
            return mapper.readValue(value == null || String.valueOf(value).isBlank() ? "{}" : String.valueOf(value), MAP);
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }

    private List<Map<String, Object>> jsonList(Object value) {
        if (value instanceof List<?> list) {
            List<Map<String, Object>> copy = new ArrayList<>();
            for (Object item : list) if (item instanceof Map<?, ?> map) {
                Map<String, Object> row = new LinkedHashMap<>();
                map.forEach((key, entry) -> row.put(String.valueOf(key), entry));
                copy.add(row);
            }
            return copy;
        }
        try {
            return mapper.readValue(value == null || String.valueOf(value).isBlank() ? "[]" : String.valueOf(value), LIST);
        } catch (Exception ignored) {
            return List.of();
        }
    }

    private static long number(Object value) {
        return value instanceof Number number ? number.longValue() : 0;
    }
}
