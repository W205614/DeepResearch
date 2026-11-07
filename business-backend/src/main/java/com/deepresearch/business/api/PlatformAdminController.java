package com.deepresearch.business.api;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.security.PlatformAccess;
import com.deepresearch.business.security.SharedApiRateLimiter;
import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.service.OutboxService;
import com.deepresearch.business.service.PageCursor;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class PlatformAdminController {
    public record StatusRequest(@NotBlank @Pattern(regexp = "active|disabled") String status,
                                @NotBlank @Size(max = 1000) String reason) {}
    public record TransferRequest(@NotBlank @Size(max = 128) String targetSubject,
                                  @NotBlank @Size(max = 1000) String reason) {}
    public record Reason(@NotBlank @Size(max = 1000) String reason) {}
    private final JdbcTemplate jdbc;
    private final PlatformAccess platform;
    private final DeepResearchProperties properties;
    private final SharedApiRateLimiter limiter;
    private final OutboxService outbox;

    public PlatformAdminController(JdbcTemplate jdbc, PlatformAccess platform, DeepResearchProperties properties,
                                   SharedApiRateLimiter limiter, OutboxService outbox) {
        this.jdbc = jdbc;
        this.platform = platform;
        this.properties = properties;
        this.limiter = limiter;
        this.outbox = outbox;
    }

    @GetMapping("/api/auth/session")
    Map<String, Object> session(@AuthenticationPrincipal Jwt jwt) {
        return Map.of("subject", jwt.getSubject(), "status", "active", "platform_operator", platform.operator(jwt.getSubject()));
    }

    @GetMapping("/api/admin/overview")
    Map<String, Object> overview(@AuthenticationPrincipal Jwt jwt) {
        platform.requireOperator(jwt);
        Map<String, Long> runs = counts("SELECT status,COUNT(*) AS total FROM runs GROUP BY status");
        Map<String, Long> users = counts("SELECT status,COUNT(*) AS total FROM platform_users GROUP BY status");
        List<Map<String, Object>> workspaces = jdbc.queryForList("""
            SELECT w.id,w.name,w.created_by,COALESCE(NULLIF(w.owner_subject,''),w.created_by) AS owner_subject,
              COALESCE(NULLIF(p.display_name,''),COALESCE(NULLIF(w.owner_subject,''),w.created_by)) AS owner_name,
              (SELECT COUNT(*) FROM memberships m LEFT JOIN platform_users u ON u.subject=m.subject
                WHERE m.workspace_id=w.id AND m.role='admin' AND COALESCE(u.status,'active')='active') AS active_admins
            FROM workspaces w LEFT JOIN platform_users p ON p.subject=COALESCE(NULLIF(w.owner_subject,''),w.created_by)
            ORDER BY w.created_at DESC,w.id LIMIT 100""");
        List<Map<String, Object>> periods = jdbc.queryForList("""
            SELECT period_start,calls,reserved_tokens,pending_tokens,unknown_tokens,actual_input,actual_output
            FROM usage_periods WHERE scope_key='platform' ORDER BY period_start DESC LIMIT 1""");
        Map<String, Object> usage = new LinkedHashMap<>();
        usage.put("recorded", !periods.isEmpty());
        usage.put("period_start", null);
        for (String field : List.of("calls", "reserved_tokens", "pending_tokens", "unknown_tokens", "actual_input", "actual_output"))
            usage.put(field, 0L);
        if (!periods.isEmpty()) usage.putAll(periods.getFirst());
        boolean redisAvailable = limiter.available();
        List<String> readOnlyReasons = new ArrayList<>();
        if (!redisAvailable) readOnlyReasons.add("redis_unavailable");
        if (platform.migrationBarrierActive()) readOnlyReasons.add("embedding_migration_active");
        Map<String, String> dependencies = Map.of("database", "reachable", "redis", redisAvailable ? "reachable" : "unavailable",
            "agent", "not_checked", "vector_store", "not_checked", "object_store", "not_checked");
        return Map.of("operator", true, "read_only", !readOnlyReasons.isEmpty(), "runs", runs, "users", users,
            "limits", Map.of("active_runs", properties.getPlatformActiveRunLimit(), "queued_runs", properties.getMaxQueuedRuns(),
                              "workspace_concurrent_runs", properties.getWorkspaceConcurrentRunLimit()),
            "workspaces", workspaces, "recent_runs", jdbc.queryForList("""
                SELECT id,user_id,created_by,status,created_at,updated_at FROM runs ORDER BY created_at DESC,id DESC LIMIT 20"""),
            "usage", usage, "dependencies", dependencies, "read_only_reasons", readOnlyReasons);
    }

    @GetMapping("/api/admin/users")
    Map<String, Object> users(@AuthenticationPrincipal Jwt jwt, @RequestParam(required = false) String cursor,
                              @RequestParam(defaultValue = "20") int limit) {
        platform.requireOperator(jwt);
        if (limit < 1 || limit > 100) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "每页数量须为 1 到 100");
        String after = "";
        if (cursor != null && !cursor.isBlank()) {
            try {
                after = new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8);
                if (after.isEmpty() || after.length() > 128) throw new IllegalArgumentException();
            } catch (IllegalArgumentException invalid) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "分页游标无效"); }
        }
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT p.*,(SELECT COUNT(*) FROM workspaces w WHERE COALESCE(NULLIF(w.owner_subject,''),w.created_by)=p.subject) AS owned_workspaces,
              (SELECT COUNT(*) FROM memberships m WHERE m.subject=p.subject) AS membership_count
            FROM platform_users p WHERE p.subject>? ORDER BY p.subject LIMIT ?""", after, limit + 1);
        String next = rows.size() > limit ? Base64.getUrlEncoder().withoutPadding().encodeToString(
            String.valueOf(rows.get(limit - 1).get("subject")).getBytes(StandardCharsets.UTF_8)) : null;
        return page(rows.subList(0, Math.min(limit, rows.size())), next);
    }

    @PutMapping("/api/admin/users/{subject}/status")
    @Transactional
    Map<String, Object> status(@AuthenticationPrincipal Jwt jwt, @PathVariable String subject,
                               @Valid @RequestBody StatusRequest request) {
        lockAdmin(jwt);
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT * FROM platform_users WHERE subject=? FOR UPDATE", subject);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "本应用尚未记录该账号");
        Map<String, Object> user = rows.getFirst();
        if (request.status().equals(user.get("status"))) return changedUser(subject);
        if (request.status().equals("disabled")) {
            if (subject.equals(jwt.getSubject())) throw new ResponseStatusException(HttpStatus.CONFLICT, "不能停用当前操作账号");
            if (jdbc.queryForObject("SELECT COUNT(*) FROM workspaces WHERE COALESCE(NULLIF(owner_subject,''),created_by)=?", Integer.class, subject) > 0)
                throw new ResponseStatusException(HttpStatus.CONFLICT, "请先交接该账号拥有的工作空间");
            if (platform.operator(subject)) {
                String placeholders = String.join(",", java.util.Collections.nCopies(properties.operatorSubjects().size(), "?"));
                var arguments = new java.util.ArrayList<Object>(properties.operatorSubjects());
                arguments.add(subject);
                int others = jdbc.queryForObject("SELECT COUNT(*) FROM platform_users WHERE status='active' AND subject IN ("
                    + placeholders + ") AND subject<>?", Integer.class, arguments.toArray());
                if (others == 0) throw new ResponseStatusException(HttpStatus.CONFLICT, "必须保留至少一名有效平台管理员");
            }
            if (jdbc.queryForObject("""
                SELECT COUNT(*) FROM memberships m WHERE m.subject=? AND m.role='admin' AND NOT EXISTS(
                  SELECT 1 FROM memberships other LEFT JOIN platform_users u ON u.subject=other.subject
                  WHERE other.workspace_id=m.workspace_id AND other.role='admin' AND other.subject<>m.subject
                    AND COALESCE(u.status,'active')='active')""", Integer.class, subject) > 0)
                throw new ResponseStatusException(HttpStatus.CONFLICT, "请先为该账号管理的空间保留另一名有效管理员");
        }
        jdbc.update("UPDATE platform_users SET status=?,revoked_before=GREATEST(revoked_before,?),updated_at=? WHERE subject=?",
            request.status(), Instant.now().getEpochSecond(), WorkspaceAccess.now(), subject);
        if (request.status().equals("disabled")) {
            List<Map<String, Object>> active = jdbc.queryForList("""
                SELECT id FROM runs WHERE created_by=? AND status IN ('queued','running','interrupted') ORDER BY id FOR UPDATE""", subject);
            for (Map<String, Object> run : active) cancelRun(String.valueOf(run.get("id")));
        }
        audit(jwt.getSubject(), "account." + request.status(), "account", subject, request.reason());
        return changedUser(subject);
    }

    @PostMapping("/api/admin/workspaces/{workspaceId}/transfer")
    @Transactional
    Map<String, Object> transfer(@AuthenticationPrincipal Jwt jwt, @PathVariable String workspaceId,
                                 @Valid @RequestBody TransferRequest request) {
        lockAdmin(jwt);
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT COALESCE(NULLIF(owner_subject,''),created_by) AS owner FROM workspaces WHERE id=? FOR UPDATE""", workspaceId);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "工作空间不存在");
        String previous = String.valueOf(rows.getFirst().get("owner"));
        if (previous.equals(request.targetSubject())) throw new ResponseStatusException(HttpStatus.CONFLICT, "目标账号已是当前所有者");
        if (jdbc.queryForObject("SELECT COUNT(*) FROM platform_users WHERE subject=? AND status='active'", Integer.class, request.targetSubject()) != 1)
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "新所有者必须是本应用已知的有效账号");
        jdbc.update("""
            INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,'admin',?)
            ON CONFLICT(workspace_id,subject) DO UPDATE SET role='admin'""", workspaceId, request.targetSubject(), WorkspaceAccess.now());
        jdbc.update("UPDATE memberships SET role='researcher' WHERE workspace_id=? AND subject=?", workspaceId, previous);
        jdbc.update("UPDATE workspaces SET owner_subject=? WHERE id=?", request.targetSubject(), workspaceId);
        audit(jwt.getSubject(), "workspace.transfer", "workspace", workspaceId, request.reason());
        return Map.of("ok", true, "workspace_id", workspaceId, "owner_subject", request.targetSubject(), "previous_owner", previous);
    }

    @PostMapping("/api/admin/runs/{runId}/cancel")
    @Transactional
    Map<String, Object> cancel(@AuthenticationPrincipal Jwt jwt, @PathVariable String runId,
                               @Valid @RequestBody Reason request) {
        lockAdmin(jwt);
        List<String> rows = jdbc.queryForList("SELECT status FROM runs WHERE id=? FOR UPDATE", String.class, runId);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在");
        cancelRun(runId);
        audit(jwt.getSubject(), "research.cancel", "run", runId, request.reason());
        return Map.of("ok", true, "run_id", runId,
            "status", jdbc.queryForObject("SELECT status FROM runs WHERE id=?", String.class, runId));
    }

    @GetMapping("/api/admin/audit")
    Map<String, Object> audit(@AuthenticationPrincipal Jwt jwt, @RequestParam(required = false) String cursor,
                              @RequestParam(defaultValue = "20") int limit,
                              @RequestParam(defaultValue = "") String actor,
                              @RequestParam(defaultValue = "") String action,
                              @RequestParam(defaultValue = "") String from,
                              @RequestParam(defaultValue = "") String until) {
        platform.requireOperator(jwt);
        if (actor.length() > 128 || action.length() > 100) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "审计过滤条件过长");
        try {
            if (!from.isEmpty()) from = Instant.parse(from).toString();
            if (!until.isEmpty()) until = Instant.parse(until).toString();
            if (!from.isEmpty() && !until.isEmpty() && Instant.parse(from).isAfter(Instant.parse(until)))
                throw new IllegalArgumentException();
        } catch (java.time.DateTimeException | IllegalArgumentException invalid) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "审计时间范围须为有效且有序的 UTC 时间");
        }
        String[] keys = PageCursor.decode(cursor, limit);
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT * FROM platform_audit_logs WHERE (?='' OR actor_subject=?) AND (?='' OR action=?)
              AND (?='' OR created_at>=?) AND (?='' OR created_at<=?)
              AND (?='' OR (created_at,id)<(?,?)) ORDER BY created_at DESC,id DESC LIMIT ?""",
            actor, actor, action, action, from, from, until, until, keys[1], keys[0], keys[1], limit + 1);
        String next = rows.size() > limit ? PageCursor.encode(rows.get(limit - 1)) : null;
        return page(rows.subList(0, Math.min(limit, rows.size())), next);
    }

    private void lockAdmin(Jwt jwt) {
        platform.requireOperator(jwt);
        platform.lockGovernance();
        // Authorization may change while another governance transaction holds the lock.
        platform.requireOperator(jwt);
    }

    private void cancelRun(String runId) {
        if (jdbc.update("UPDATE runs SET status='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running','interrupted')",
                WorkspaceAccess.now(), runId) > 0) {
            jdbc.update("INSERT INTO events(run_id,type,data,created_at) VALUES(?,'cancelled',?,?)", runId,
                "{\"message\":\"平台管理员已停止此任务\"}", WorkspaceAccess.now());
            outbox.enqueueAbort(runId);
        }
    }

    private Map<String, Object> changedUser(String subject) {
        Map<String, Object> result = new LinkedHashMap<>(jdbc.queryForMap("SELECT * FROM platform_users WHERE subject=?", subject));
        result.put("ok", true);
        return result;
    }

    private void audit(String actor, String action, String type, String id, String reason) {
        jdbc.update("""
            INSERT INTO platform_audit_logs(id,actor_subject,action,target_type,target_id,reason,result,created_at)
            VALUES(?,?,?,?,?,?,'success',?)""", WorkspaceAccess.uid(), actor, action, type, id, reason.strip(), WorkspaceAccess.now());
    }

    private Map<String, Long> counts(String sql) {
        Map<String, Long> result = new LinkedHashMap<>();
        jdbc.queryForList(sql).forEach(row -> result.put(String.valueOf(row.get("status")), ((Number) row.get("total")).longValue()));
        return result;
    }

    private Map<String, Object> page(List<Map<String, Object>> items, String next) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("items", items);
        result.put("next_cursor", next);
        return result;
    }
}
