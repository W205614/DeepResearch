package com.deepresearch.business.api;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.AgentClient;
import com.deepresearch.business.service.ResearchCommandService;
import com.deepresearch.business.service.RunViewService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ThreadController {
    private final JdbcTemplate jdbc;
    private final WorkspaceAccess access;
    private final ResearchCommandService research;
    private final RunViewService views;
    private final AgentClient agent;

    public ThreadController(JdbcTemplate jdbc, WorkspaceAccess access, ResearchCommandService research,
                            RunViewService views, AgentClient agent) {
        this.jdbc = jdbc;
        this.access = access;
        this.research = research;
        this.views = views;
        this.agent = agent;
    }

    @PostMapping("/api/threads")
    ResponseEntity<Map<String, Object>> create(@AuthenticationPrincipal Jwt jwt,
                                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                               @Valid @RequestBody Requests.Thread body) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        return ResponseEntity.status(201).body(research.createThread(identity, body.title()));
    }

    @GetMapping("/api/threads")
    List<Map<String, Object>> list(@AuthenticationPrincipal Jwt jwt,
                                   @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        return jdbc.queryForList("SELECT * FROM threads WHERE user_id=? ORDER BY created_at DESC", identity.workspaceId());
    }

    @PatchMapping("/api/threads/{threadId}")
    @Transactional
    Map<String, Boolean> rename(@PathVariable String threadId, @AuthenticationPrincipal Jwt jwt,
                                @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                @Valid @RequestBody Requests.Thread body) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        access.require(identity, "admin", "researcher");
        Map<String, Object> thread = research.resolveThread(identity.workspaceId(), threadId);
        int changed = jdbc.update("UPDATE threads SET title=? WHERE id=? AND user_id=?", body.title(), thread.get("id"), identity.workspaceId());
        if (changed == 0) throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND, "会话不存在");
        return Map.of("ok", true);
    }

    @DeleteMapping("/api/threads/{threadId}")
    ResponseEntity<byte[]> delete(@PathVariable String threadId, @AuthenticationPrincipal Jwt jwt,
                                  @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                  HttpServletRequest request) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        access.require(identity, "admin");
        research.resolveThread(identity.workspaceId(), threadId);
        return forwarded(agent.forward(request, new byte[0]));
    }

    @GetMapping("/api/threads/{threadId}/runs")
    List<Map<String, Object>> runs(@PathVariable String threadId, @AuthenticationPrincipal Jwt jwt,
                                   @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        Map<String, Object> thread = research.resolveThread(identity.workspaceId(), threadId);
        return views.threadRuns(String.valueOf(thread.get("id")), identity.workspaceId());
    }

    @GetMapping("/api/threads/{threadId}/report")
    ResponseEntity<byte[]> report(@PathVariable String threadId, @AuthenticationPrincipal Jwt jwt,
                                  @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        Map<String, Object> thread = research.resolveThread(identity.workspaceId(), threadId);
        List<Map<String, Object>> runs = views.threadRuns(String.valueOf(thread.get("id")), identity.workspaceId());
        StringBuilder report = new StringBuilder("# ").append(thread.get("title") == null ? "研究会话" : thread.get("title"))
            .append("\n\n本文件包含该会话中的全部研究记录。\n");
        for (int index = 0; index < runs.size(); index++) {
            Map<String, Object> run = runs.get(index);
            report.append("\n## ").append(index + 1).append(". ").append(run.get("topic"))
                .append("\n\n- 状态：").append(run.get("status"))
                .append("\n- 创建时间：").append(run.get("created_at")).append("\n\n")
                .append(String.valueOf(run.get("report")).isBlank() ? "_此研究尚未生成 Markdown 报告。_" : run.get("report")).append('\n');
        }
        return ResponseEntity.ok()
            .contentType(MediaType.parseMediaType("text/markdown;charset=UTF-8"))
            .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"conversation-" + thread.get("id") + ".md\"")
            .body(report.toString().getBytes(StandardCharsets.UTF_8));
    }

    static ResponseEntity<byte[]> forwarded(AgentClient.AgentResponse response) {
        return ResponseEntity.status(response.status()).headers(response.headers()).body(response.body());
    }
}
