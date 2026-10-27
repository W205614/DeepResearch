package com.deepresearch.business.service;

import java.util.List;
import java.util.Map;
import com.deepresearch.business.security.WorkspaceIdentity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

/** Current document permissions govern live and frozen evidence visibility; snapshots stay immutable. */
public final class DocumentAccessService {
    private DocumentAccessService() {}
    public static boolean allowed(JdbcTemplate jdbc, WorkspaceIdentity identity, String documentId) {
        if(documentId==null || documentId.isBlank()) return false;
        List<Map<String,Object>> memberships=jdbc.queryForList("SELECT role FROM memberships WHERE workspace_id=? AND subject=?",
            identity.workspaceId(),identity.subject());
        if(memberships.isEmpty()) return false;
        List<Map<String,Object>> rows=jdbc.queryForList("""
            SELECT p.workspace_id,p.owner_subject,p.collection_id,c.visibility FROM document_policies p
            LEFT JOIN collections c ON c.id=p.collection_id AND c.workspace_id=p.workspace_id
            WHERE p.document_id=?""",documentId);
        if(rows.isEmpty()) return !jdbc.queryForList("SELECT id FROM documents WHERE id=? AND user_id=?",documentId,identity.workspaceId()).isEmpty();
        Map<String,Object> row=rows.getFirst();
        if(!identity.workspaceId().equals(row.get("workspace_id"))) return false;
        if("admin".equals(memberships.getFirst().get("role"))) return true;
        if("".equals(row.get("collection_id")) || "workspace".equals(row.get("visibility"))
            || identity.subject().equals(row.get("owner_subject"))) return true;
        return !jdbc.queryForList("SELECT subject FROM collection_grants WHERE collection_id=? AND subject=?",
            row.get("collection_id"),identity.subject()).isEmpty();
    }
    public static boolean sourcesAllowed(JdbcTemplate jdbc,ObjectMapper mapper,WorkspaceIdentity identity,Object raw) {
        List<Map<String,Object>> sources;
        try { sources=mapper.readValue(String.valueOf(raw),new TypeReference<List<Map<String,Object>>>(){}); }
        catch(Exception error) { return false; }
        return sources.stream().filter(s->"local".equals(s.get("kind")))
            .allMatch(s->allowed(jdbc,identity,String.valueOf(s.getOrDefault("document_id",""))));
    }
    public static void requireSources(JdbcTemplate jdbc,ObjectMapper mapper,WorkspaceIdentity identity,Object raw) {
        if(!sourcesAllowed(jdbc,mapper,identity,raw))
            throw new ResponseStatusException(HttpStatus.NOT_FOUND,"研究或报告不存在，或资料访问权限已撤销");
    }
}
