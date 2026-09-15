package com.deepresearch.business.security;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import com.deepresearch.business.config.DeepResearchProperties;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Component
public class WorkspaceAccess {
    private final JdbcTemplate jdbc;
    private final DeepResearchProperties properties;

    public WorkspaceAccess(JdbcTemplate jdbc, DeepResearchProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @Transactional
    public WorkspaceIdentity resolve(Jwt jwt, String requestedWorkspace) {
        String subject = jwt.getSubject();
        String displayName = claim(jwt, "preferred_username", claim(jwt, "name", subject));
        List<Map<String, Object>> memberships = memberships(subject);
        if (memberships.isEmpty()) {
            String stamp = now();
            jdbc.update("INSERT INTO workspaces(id,name,created_by,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
                subject, displayName + " 的工作空间", subject, stamp);
            jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?) ON CONFLICT(workspace_id,subject) DO NOTHING",
                subject, subject, "admin", stamp);
            jdbc.update("""
                INSERT INTO workspace_limits(workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit)
                VALUES(?,0,0,?) ON CONFLICT(workspace_id) DO NOTHING""", subject, properties.getWorkspaceConcurrentRunLimit());
            memberships = memberships(subject);
        }
        Map<String, Object> selected;
        if (requestedWorkspace != null && !requestedWorkspace.isBlank()) {
            selected = memberships.stream().filter(row -> requestedWorkspace.equals(row.get("id"))).findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.FORBIDDEN, "你不是该工作空间成员"));
        } else {
            selected = memberships.getFirst();
        }
        return new WorkspaceIdentity(String.valueOf(selected.get("id")), subject,
            String.valueOf(selected.get("role")), displayName);
    }

    public List<Map<String, Object>> memberships(String subject) {
        return jdbc.queryForList("""
            SELECT w.id,w.name,m.role,w.created_at FROM workspaces w
            JOIN memberships m ON w.id=m.workspace_id WHERE m.subject=? ORDER BY w.created_at""", subject);
    }

    public void require(WorkspaceIdentity identity, String... roles) {
        if (!identity.hasRole(roles)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "当前工作空间角色没有此权限");
        }
    }

    public void audit(WorkspaceIdentity identity, String action, String targetType, String targetId) {
        jdbc.update("""
            INSERT INTO audit_logs(id,workspace_id,actor_subject,action,target_type,target_id,result,created_at)
            VALUES(?,?,?,?,?,?,?,?)""", uid(), identity.workspaceId(), identity.subject(), action, targetType, targetId,
            "success", now());
    }

    private static String claim(Jwt jwt, String name, String fallback) {
        Object value = jwt.getClaims().get(name);
        return value == null || String.valueOf(value).isBlank() ? fallback : String.valueOf(value);
    }

    public static String uid() {
        return UUID.randomUUID().toString().replace("-", "");
    }

    public static String now() {
        return Instant.now().toString();
    }
}
