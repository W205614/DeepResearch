package com.deepresearch.business.service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.security.WorkspaceIdentity;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;
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

    public Map<String, Object> visibleRun(String runId, WorkspaceIdentity identity) {
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT * FROM runs WHERE id=? AND user_id=?",
            runId, identity.workspaceId());
        if (rows.isEmpty() || !identity.hasRole("admin")
            && !identity.subject().equals(rows.getFirst().get("created_by")))
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在");
        DocumentAccessService.requireSources(jdbc, mapper, identity, rows.getFirst().get("sources"));
        return render(rows.getFirst());
    }

    public List<Map<String, Object>> visibleThreadRuns(String threadId, WorkspaceIdentity identity) {
        return renderMany(jdbc.queryForList("""
            SELECT * FROM runs WHERE thread_id=? AND user_id=?
            AND (? OR created_by=?) ORDER BY created_at""",
            threadId, identity.workspaceId(), identity.hasRole("admin"), identity.subject()).stream()
            .filter(row -> DocumentAccessService.sourcesAllowed(jdbc, mapper, identity, row.get("sources"))).toList());
    }

    public List<Map<String, Object>> threadRuns(String threadId, String workspaceId) {
        return renderMany(jdbc.queryForList("SELECT * FROM runs WHERE thread_id=? AND user_id=? ORDER BY created_at", threadId, workspaceId));
    }

    public ReportPublicationService.Page page(String threadId, WorkspaceIdentity identity, String cursor, int limit) {
        String[] keys = PageCursor.decode(cursor, limit);
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT * FROM runs WHERE thread_id=? AND user_id=? AND (? OR created_by=?)
            AND (?='' OR (created_at,id)<(?,?)) ORDER BY created_at DESC,id DESC LIMIT ?""",
            threadId, identity.workspaceId(), identity.hasRole("admin"), identity.subject(), keys[1], keys[0], keys[1], limit + 1);
        String next = rows.size() > limit ? PageCursor.encode(rows.get(limit - 1)) : null;
        return new ReportPublicationService.Page(renderMany(rows.subList(0, Math.min(limit, rows.size())).stream()
            .filter(row -> DocumentAccessService.sourcesAllowed(jdbc, mapper, identity, row.get("sources"))).toList()), next);
    }

    public Map<String, Object> render(Map<String, Object> source) {
        return renderMany(List.of(source)).getFirst();
    }

    private List<Map<String, Object>> renderMany(List<Map<String, Object>> sources) {
        if (sources.isEmpty()) return List.of();
        Object[] ids = sources.stream().map(row -> row.get("id")).toArray();
        String placeholders = String.join(",", java.util.Collections.nCopies(ids.length, "?"));
        Map<String, Map<String, Object>> publications = new LinkedHashMap<>(), usage = new LinkedHashMap<>();
        Map<String, List<Map<String, Object>>> attachments = new LinkedHashMap<>();
        jdbc.queryForList("SELECT source_run_id,id,status,series_id,revision_no,reviewed_by,reviewed_at,review_reason,withdrawn_at,withdrawal_reason FROM report_publications WHERE source_run_id IN (" + placeholders + ")", ids)
            .forEach(row -> publications.put(String.valueOf(row.get("source_run_id")), row));
        jdbc.queryForList("SELECT * FROM counters WHERE run_id IN (" + placeholders + ")", ids)
            .forEach(row -> usage.put(String.valueOf(row.get("run_id")), row));
        jdbc.queryForList("SELECT run_id,id,name,media_type,size,position FROM attachments WHERE status='ready' AND run_id IN (" + placeholders + ") ORDER BY position", ids)
            .forEach(row -> attachments.computeIfAbsent(String.valueOf(row.get("run_id")), ignored -> new ArrayList<>()).add(row));
        return sources.stream().map(source -> render(source, publications, usage, attachments)).toList();
    }

    private Map<String, Object> render(Map<String, Object> source, Map<String, Map<String, Object>> publications,
                                      Map<String, Map<String, Object>> usage, Map<String, List<Map<String, Object>>> attachments) {
        Map<String, Object> row = new LinkedHashMap<>(source);
        String runId = String.valueOf(row.get("id"));
        row.put("sources", jsonList(row.get("sources")));
        Map<String, Object> validation = jsonMap(row.get("validation"));
        validation.putIfAbsent("quality", "unknown");
        row.put("validation", validation);
        row.put("publication", publications.get(runId));
        Map<String, Object> errorInfo = jsonMap(row.get("error_info"));
        row.put("error_info", errorInfo);
        row.put("attachments", attachments.getOrDefault(runId, List.of()));
        row.put("usage", usage.getOrDefault(runId, Map.of()));
        long deadline = number(row.get("deadline_at"));
        long calls = number(row.get("call_attempts"));
        long reserved = number(row.get("reserved_tokens"));
        String status = String.valueOf(row.get("status"));
        boolean canResume = ResearchCommandContract.canResume(status, errorInfo.get("retryable"), deadline,
            Instant.now().getEpochSecond(), calls, reserved, properties.getMaxRunCallAttempts(), properties.getMaxRunReservedTokens());
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
