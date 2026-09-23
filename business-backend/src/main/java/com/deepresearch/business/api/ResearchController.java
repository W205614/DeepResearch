package com.deepresearch.business.api;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.AgentClient;
import com.deepresearch.business.service.ResearchCommandService;
import com.deepresearch.business.service.RunViewService;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
public class ResearchController {
    private static final List<String> TERMINAL = List.of("completed", "insufficient", "failed", "cancelled", "interrupted");
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {};
    private final JdbcTemplate jdbc;
    private final WorkspaceAccess access;
    private final ResearchCommandService commands;
    private final RunViewService views;
    private final AgentClient agent;
    private final ObjectMapper mapper;

    public ResearchController(JdbcTemplate jdbc, WorkspaceAccess access, ResearchCommandService commands,
                              RunViewService views, AgentClient agent, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.access = access;
        this.commands = commands;
        this.views = views;
        this.agent = agent;
        this.mapper = mapper;
    }

    @PostMapping("/api/research/runs")
    ResponseEntity<Map<String, Object>> create(@AuthenticationPrincipal Jwt jwt,
                                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                               @Valid @RequestBody Requests.Run body) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        return ResponseEntity.accepted().body(commands.create(identity, body));
    }

    @GetMapping("/api/research/runs/{runId}")
    Map<String, Object> get(@PathVariable String runId, @AuthenticationPrincipal Jwt jwt,
                            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        return views.visibleRun(runId, identity);
    }

    @GetMapping("/api/research/runs/{runId}/report")
    ResponseEntity<byte[]> report(@PathVariable String runId, @AuthenticationPrincipal Jwt jwt,
                                  @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        Map<String, Object> run = views.visibleRun(runId, identity);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("text/markdown;charset=UTF-8"))
            .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"research-" + runId + ".md\"")
            .body(String.valueOf(run.get("report")).getBytes(StandardCharsets.UTF_8));
    }

    @PostMapping("/api/research/runs/{runId}/cancel")
    Map<String, Object> cancel(@PathVariable String runId, @AuthenticationPrincipal Jwt jwt,
                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return commands.cancel(access.resolve(jwt, requested), runId);
    }

    @PostMapping("/api/research/runs/{runId}/resume")
    ResponseEntity<Map<String, Object>> resume(@PathVariable String runId, @AuthenticationPrincipal Jwt jwt,
                                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return ResponseEntity.accepted().body(commands.resume(access.resolve(jwt, requested), runId));
    }

    @PostMapping("/api/research/runs/{runId}/memory")
    ResponseEntity<byte[]> saveMemory(@PathVariable String runId, @AuthenticationPrincipal Jwt jwt,
                                      @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                      HttpServletRequest request) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        views.visibleRun(runId, identity);
        return ThreadController.forwarded(agent.forward(request, new byte[0]));
    }

    @GetMapping(value = "/api/research/runs/{runId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    SseEmitter events(@PathVariable String runId, @RequestParam(defaultValue = "0") long after,
                      @RequestHeader(value = "Last-Event-ID", defaultValue = "0") String lastEventId,
                      @AuthenticationPrincipal Jwt jwt,
                      @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        views.visibleRun(runId, identity);
        long headerCursor;
        try { headerCursor = Long.parseLong(lastEventId); }
        catch (NumberFormatException error) { throw new IllegalArgumentException("事件序号无效"); }
        long[] cursor = {Math.max(0, Math.max(after, headerCursor))};
        SseEmitter emitter = new SseEmitter(0L);
        Thread.startVirtualThread(() -> streamEvents(emitter, identity, runId, cursor));
        return emitter;
    }

    private void streamEvents(SseEmitter emitter, WorkspaceIdentity identity, String runId, long[] cursor) {
        int ticks = 0;
        try {
            while (true) {
                if (jdbc.queryForObject("""
                    SELECT COUNT(*) FROM memberships m JOIN runs r ON r.user_id=m.workspace_id
                    WHERE m.workspace_id=? AND m.subject=? AND r.id=?
                    AND (m.role='admin' OR r.created_by=m.subject)""",
                    Integer.class, identity.workspaceId(), identity.subject(), runId) == 0) {
                    emitter.send(SseEmitter.event().name("close").data(Map.of()));
                    emitter.complete();
                    return;
                }
                List<Map<String, Object>> rows = jdbc.queryForList(
                    "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 200", runId, cursor[0]);
                for (Map<String, Object> row : rows) {
                    cursor[0] = ((Number) row.get("id")).longValue();
                    Map<String, Object> payload = new LinkedHashMap<>();
                    payload.put("id", cursor[0]);
                    payload.put("type", row.get("type"));
                    payload.put("created_at", row.get("created_at"));
                    payload.put("data", mapper.readValue(String.valueOf(row.get("data")), MAP));
                    emitter.send(SseEmitter.event().id(String.valueOf(cursor[0])).data(payload, MediaType.APPLICATION_JSON));
                }
                List<Map<String, Object>> current = jdbc.queryForList(
                    "SELECT status FROM runs WHERE id=? AND user_id=?", runId, identity.workspaceId());
                if (current.isEmpty() || TERMINAL.contains(String.valueOf(current.getFirst().get("status"))) && rows.isEmpty()) {
                    emitter.send(SseEmitter.event().name("close").data(Map.of()));
                    emitter.complete();
                    return;
                }
                if (ticks++ % 20 == 0) emitter.send(SseEmitter.event().comment("keep-alive"));
                Thread.sleep(500);
            }
        } catch (Exception disconnected) {
            emitter.complete();
        }
    }
}
