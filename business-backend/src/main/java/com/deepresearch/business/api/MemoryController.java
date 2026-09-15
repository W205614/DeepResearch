package com.deepresearch.business.api;

import static com.deepresearch.business.security.WorkspaceAccess.now;
import static com.deepresearch.business.security.WorkspaceAccess.uid;

import java.util.List;
import java.util.Map;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.AgentClient;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class MemoryController {
    private final JdbcTemplate jdbc;
    private final WorkspaceAccess access;
    private final AgentClient agent;

    public MemoryController(JdbcTemplate jdbc, WorkspaceAccess access, AgentClient agent) {
        this.jdbc = jdbc;
        this.access = access;
        this.agent = agent;
    }

    @GetMapping("/api/memories")
    List<Map<String, Object>> list(@AuthenticationPrincipal Jwt jwt,
                                   @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        return jdbc.queryForList("""
            SELECT id,kind,content,created_at FROM memories WHERE
            (kind IN ('profile','preference') AND owner_subject=?) OR (kind='semantic' AND user_id=? )
            ORDER BY created_at DESC""", identity.subject(), identity.workspaceId());
    }

    @PostMapping("/api/memories")
    @Transactional
    ResponseEntity<Map<String, Object>> add(@AuthenticationPrincipal Jwt jwt,
                                            @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                            @Valid @RequestBody Requests.Memory body) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        access.require(identity, "admin", "researcher");
        String id = uid();
        jdbc.update("""
            INSERT INTO memories(id,user_id,kind,content,run_id,created_at,owner_subject)
            VALUES(?,?,'preference',?,?,?,?)""", id, identity.workspaceId(), body.content(), id, now(), identity.subject());
        return ResponseEntity.status(201).body(Map.of("id", id, "content", body.content()));
    }

    @PutMapping("/api/memories/{memoryId}")
    @Transactional
    Map<String, Boolean> edit(@PathVariable String memoryId, @AuthenticationPrincipal Jwt jwt,
                              @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                              @Valid @RequestBody Requests.Memory body) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        access.require(identity, "admin", "researcher");
        int changed = jdbc.update("UPDATE memories SET content=? WHERE id=? AND owner_subject=? AND kind='preference'",
            body.content(), memoryId, identity.subject());
        if (changed == 0) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "可编辑的用户偏好不存在");
        return Map.of("ok", true);
    }

    @DeleteMapping("/api/memories/{memoryId}")
    @Transactional
    ResponseEntity<?> delete(@PathVariable String memoryId, @AuthenticationPrincipal Jwt jwt,
                             @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                             HttpServletRequest request) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        access.require(identity, "admin", "researcher");
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT * FROM memories WHERE id=? AND (
            (kind IN ('profile','preference') AND owner_subject=?) OR (kind='semantic' AND user_id=?))""",
            memoryId, identity.subject(), identity.workspaceId());
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "记忆不存在");
        if ("semantic".equals(rows.getFirst().get("kind")))
            return ThreadController.forwarded(agent.forward(request, new byte[0]));
        jdbc.update("DELETE FROM memories WHERE id=?", memoryId);
        return ResponseEntity.ok(Map.of("ok", true));
    }
}
