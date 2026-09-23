package com.deepresearch.business.api;

import static com.deepresearch.business.security.WorkspaceAccess.now;
import static com.deepresearch.business.security.WorkspaceAccess.uid;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.OutboxService;
import com.deepresearch.business.service.ResearchCommandService;
import com.deepresearch.business.service.RunViewService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class WorkspaceController {
    private final JdbcTemplate jdbc;
    private final WorkspaceAccess access;
    private final DeepResearchProperties properties;
    private final ResearchCommandService research;
    private final RunViewService views;
    private final OutboxService outbox;

    public WorkspaceController(JdbcTemplate jdbc, WorkspaceAccess access, DeepResearchProperties properties,
                               ResearchCommandService research, RunViewService views, OutboxService outbox) {
        this.jdbc = jdbc;
        this.access = access;
        this.properties = properties;
        this.research = research;
        this.views = views;
        this.outbox = outbox;
    }

    @GetMapping("/api/workspaces")
    List<Map<String, Object>> list(@AuthenticationPrincipal Jwt jwt,
                                   @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        access.resolve(jwt, null);
        return access.memberships(jwt.getSubject());
    }

    @PostMapping("/api/workspaces")
    @ResponseStatus(HttpStatus.CREATED)
    @Transactional
    Map<String, Object> create(@AuthenticationPrincipal Jwt jwt,
                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                               @Valid @RequestBody Requests.Workspace body) {
        access.resolve(jwt, requested);
        String id = uid(), stamp = now();
        jdbc.update("INSERT INTO workspaces(id,name,created_by,created_at) VALUES(?,?,?,?)", id, body.name().strip(), jwt.getSubject(), stamp);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?)", id, jwt.getSubject(), "admin", stamp);
        jdbc.update("""
            INSERT INTO workspace_limits(workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit)
            VALUES(?,0,0,?)""", id, properties.getWorkspaceConcurrentRunLimit());
        WorkspaceIdentity identity = new WorkspaceIdentity(id, jwt.getSubject(), "admin", jwt.getSubject());
        access.audit(identity, "workspace.create", "workspace", id);
        return Map.of("id", id, "name", body.name().strip(), "created_by", jwt.getSubject(), "created_at", stamp);
    }

    @GetMapping("/api/workspaces/{workspaceId}/members")
    List<Map<String, Object>> members(@PathVariable String workspaceId, @AuthenticationPrincipal Jwt jwt,
                                      @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        return jdbc.queryForList("SELECT subject,role,created_at FROM memberships WHERE workspace_id=? ORDER BY created_at", workspaceId);
    }

    @PutMapping("/api/workspaces/{workspaceId}/members")
    @Transactional
    Map<String, Boolean> upsertMember(@PathVariable String workspaceId, @AuthenticationPrincipal Jwt jwt,
                                      @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                      @Valid @RequestBody Requests.Membership body) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        jdbc.update("""
            INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?)
            ON CONFLICT(workspace_id,subject) DO UPDATE SET role=EXCLUDED.role""",
            workspaceId, body.subject(), body.role(), now());
        access.audit(identity, "membership.upsert", "member", body.subject());
        return Map.of("ok", true);
    }

    @GetMapping("/api/workspaces/{workspaceId}/audit")
    List<Map<String, Object>> audit(@PathVariable String workspaceId, @AuthenticationPrincipal Jwt jwt,
                                    @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        return jdbc.queryForList("""
            SELECT action,target_type,target_id,result,actor_subject,created_at FROM audit_logs
            WHERE workspace_id=? ORDER BY created_at DESC LIMIT 100""", workspaceId);
    }

    @GetMapping("/api/workspaces/{workspaceId}/dead-letters")
    List<Map<String, Object>> deadLetters(@PathVariable String workspaceId, @AuthenticationPrincipal Jwt jwt,
                                          @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        return jdbc.queryForList("""
            SELECT d.run_id,d.category,d.message,d.failed_at,d.recovered_at,r.status
            FROM dead_letter_runs d JOIN runs r ON r.id=d.run_id AND r.user_id=d.user_id
            WHERE d.user_id=? ORDER BY d.failed_at DESC""", workspaceId).stream().map(row -> {
                Map<String, Object> result = new LinkedHashMap<>(row);
                Map<String, Object> run = views.ownedRun(String.valueOf(row.get("run_id")), workspaceId);
                result.put("can_recover", run.get("can_resume"));
                String status = String.valueOf(row.get("status"));
                result.put("recovery_status", "completed".equals(status) ? "succeeded" :
                    "insufficient".equals(status) ? "insufficient" : List.of("queued", "running").contains(status) ? "retrying" : "pending");
                return result;
            }).toList();
    }

    @PostMapping("/api/workspaces/{workspaceId}/dead-letters/{runId}/recover")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, Object> recover(@PathVariable String workspaceId, @PathVariable String runId,
                                @AuthenticationPrincipal Jwt jwt,
                                @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        if (jdbc.queryForList("SELECT run_id FROM dead_letter_runs WHERE run_id=? AND user_id=?", runId, workspaceId).isEmpty())
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "死信任务不存在或已恢复");
        Map<String, Object> run = research.resume(identity, runId);
        access.audit(identity, "dead_letter.recover", "run", runId);
        return run;
    }

    @GetMapping("/api/workspaces/{workspaceId}/outbox/dead-letters")
    List<Map<String, Object>> outboxDeadLetters(@PathVariable String workspaceId,
                                                @AuthenticationPrincipal Jwt jwt,
                                                @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        return outbox.deadLetters(workspaceId);
    }

    @PostMapping("/api/workspaces/{workspaceId}/outbox/dead-letters/{messageId}/retry")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @Transactional
    Map<String, Boolean> retryOutboxDeadLetter(@PathVariable String workspaceId,
                                               @PathVariable String messageId,
                                               @AuthenticationPrincipal Jwt jwt,
                                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        if (!outbox.retryDeadLetter(messageId, workspaceId))
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Outbox 死信不存在或已经重新投递");
        access.audit(identity, "outbox.retry", "outbox_message", messageId);
        return Map.of("ok", true);
    }

    @PutMapping("/api/workspaces/{workspaceId}/limits")
    @Transactional
    Map<String, Object> limits(@PathVariable String workspaceId, @AuthenticationPrincipal Jwt jwt,
                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                               @Valid @RequestBody Requests.WorkspaceLimit body) {
        WorkspaceIdentity identity = selected(workspaceId, jwt, requested);
        access.require(identity, "admin");
        jdbc.update("UPDATE workspace_limits SET daily_search_limit=0,concurrent_run_limit=? WHERE workspace_id=?",
            body.concurrentRunLimit(), workspaceId);
        access.audit(identity, "workspace.limits.update", "workspace", workspaceId);
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("daily_search_limit", null);
        response.put("concurrent_run_limit", body.concurrentRunLimit());
        return response;
    }

    private WorkspaceIdentity selected(String pathWorkspace, Jwt jwt, String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested == null || requested.isBlank() ? pathWorkspace : requested);
        if (!pathWorkspace.equals(identity.workspaceId()))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "请先切换到目标工作空间");
        return identity;
    }
}
