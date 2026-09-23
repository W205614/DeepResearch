package com.deepresearch.business.service;

import static com.deepresearch.business.security.WorkspaceAccess.now;
import static com.deepresearch.business.security.WorkspaceAccess.uid;

import java.nio.charset.StandardCharsets;
import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class ReportPublicationService {
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {};
    private static final TypeReference<List<Map<String, Object>>> LIST = new TypeReference<>() {};
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final WorkspaceAccess access;

    public ReportPublicationService(JdbcTemplate jdbc, ObjectMapper mapper, WorkspaceAccess access) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.access = access;
    }

    @Transactional
    public Map<String, Object> submit(WorkspaceIdentity identity, String runId) {
        access.require(identity, "admin", "researcher");
        lockPublicationWorkspace(identity.workspaceId());
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT * FROM runs WHERE id=? AND user_id=? FOR UPDATE", runId, identity.workspaceId());
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在");
        Map<String, Object> run = rows.getFirst();
        if (!identity.subject().equals(run.get("created_by")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "只能提交本人创建的研究报告");
        List<Map<String, Object>> existing = jdbc.queryForList(
            "SELECT * FROM report_publications WHERE source_run_id=?", runId);
        if (!existing.isEmpty()) return detail(existing.getFirst());
        if (Boolean.TRUE.equals(run.get("report_submitted")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "该任务已经送审；请新建研究任务后重新提交");
        Map<String, Object> validation = parseMap(run.get("validation"));
        List<Map<String, Object>> sources = parseList(run.get("sources"));
        String report = String.valueOf(run.get("report") == null ? "" : run.get("report"));
        boolean hasQuotedEvidence = sources.stream().anyMatch(source -> {
            Object id = source.get("id"), text = source.get("text");
            return id instanceof String key && !key.isBlank()
                && text instanceof String excerpt && !excerpt.isBlank()
                && report.contains("[" + key + "]");
        });
        if (!"completed".equals(run.get("status")) || !"complete".equals(validation.get("quality"))
            || report.isBlank() || !hasQuotedEvidence || number(validation.get("checked_claims")) < 1
            || number(validation.get("supported_claims")) != number(validation.get("checked_claims"))) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "只有证据与引用核验完整的研究报告可提交审核");
        }
        String sourceJson = String.valueOf(run.get("sources"));
        String validationJson = String.valueOf(run.get("validation"));
        String digest = sha256(report + "\n" + sourceJson + "\n" + validationJson);
        String id = uid();
        jdbc.update("""
            INSERT INTO report_publications(id,workspace_id,source_run_id,author_subject,topic,
                report_markdown,sources_json,validation_json,content_sha256,status,submitted_at)
            VALUES(?,?,?,?,?,?,?,?,?,'pending',?)""", id, identity.workspaceId(), runId, identity.subject(),
            String.valueOf(run.get("topic")), report, sourceJson, validationJson, digest, now());
        jdbc.update("UPDATE runs SET report_submitted=TRUE WHERE id=?", runId);
        access.audit(identity, "report.submit", "report", id);
        return detail(lookup(identity.workspaceId(), id, false));
    }

    @Transactional
    public Map<String, Object> approve(WorkspaceIdentity identity, String reportId) {
        access.require(identity, "admin");
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, true);
        if (identity.subject().equals(row.get("author_subject")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "报告作者不能审核自己的报告");
        requireStatus(row, "pending");
        jdbc.update("UPDATE report_publications SET status='published',reviewed_by=?,reviewed_at=? WHERE id=?",
            identity.subject(), now(), reportId);
        access.audit(identity, "report.approve", "report", reportId);
        return detail(lookup(identity.workspaceId(), reportId, false));
    }

    @Transactional
    public Map<String, Object> reject(WorkspaceIdentity identity, String reportId, String reason) {
        access.require(identity, "admin");
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, true);
        if (identity.subject().equals(row.get("author_subject")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "报告作者不能审核自己的报告");
        requireStatus(row, "pending");
        jdbc.update("UPDATE report_publications SET status='rejected',reviewed_by=?,reviewed_at=?,review_reason=? WHERE id=?",
            identity.subject(), now(), reason.strip(), reportId);
        access.audit(identity, "report.reject", "report", reportId);
        return detail(lookup(identity.workspaceId(), reportId, false));
    }

    @Transactional
    public Map<String, Object> withdraw(WorkspaceIdentity identity, String reportId, String reason) {
        access.require(identity, "admin");
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, true);
        requireStatus(row, "published");
        jdbc.update("UPDATE report_publications SET status='withdrawn',withdrawn_by=?,withdrawn_at=?,withdrawal_reason=? WHERE id=?",
            identity.subject(), now(), reason.strip(), reportId);
        access.audit(identity, "report.withdraw", "report", reportId);
        return detail(lookup(identity.workspaceId(), reportId, false));
    }

    @Transactional
    public void delete(WorkspaceIdentity identity, String reportId) {
        access.require(identity, "admin");
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, true);
        if (!List.of("rejected", "withdrawn").contains(row.get("status")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只有已驳回或已撤回的报告可以清理");
        jdbc.update("DELETE FROM report_publications WHERE id=?", reportId);
        access.audit(identity, "report.delete", "report", reportId);
    }

    public List<Map<String, Object>> published(WorkspaceIdentity identity) {
        return jdbc.queryForList("""
            SELECT id,source_run_id,topic,author_subject,content_sha256,submitted_at,reviewed_by,reviewed_at,status
            FROM report_publications WHERE workspace_id=? AND status='published' ORDER BY reviewed_at DESC,id""",
            identity.workspaceId());
    }

    public List<Map<String, Object>> pending(WorkspaceIdentity identity) {
        access.require(identity, "admin");
        return jdbc.queryForList("""
            SELECT id,source_run_id,topic,author_subject,content_sha256,submitted_at,status
            FROM report_publications WHERE workspace_id=? AND status='pending' ORDER BY submitted_at,id""",
            identity.workspaceId());
    }

    public List<Map<String, Object>> archived(WorkspaceIdentity identity) {
        access.require(identity, "admin");
        return jdbc.queryForList("""
            SELECT id,source_run_id,topic,author_subject,submitted_at,status,reviewed_at,withdrawn_at
            FROM report_publications WHERE workspace_id=? AND status IN ('rejected','withdrawn')
            ORDER BY submitted_at DESC,id""", identity.workspaceId());
    }

    public Map<String, Object> get(WorkspaceIdentity identity, String reportId) {
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, false);
        if (!"published".equals(row.get("status")) && !identity.hasRole("admin")
            && !identity.subject().equals(row.get("author_subject")))
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "报告不存在");
        return detail(row);
    }

    public void requireNoPublicationsForThread(WorkspaceIdentity identity, String threadId) {
        int count = jdbc.queryForObject("""
            SELECT COUNT(*) FROM report_publications p JOIN runs r ON r.id=p.source_run_id
            WHERE p.workspace_id=? AND r.thread_id=?""", Integer.class, identity.workspaceId(), threadId);
        if (count > 0) throw new ResponseStatusException(HttpStatus.CONFLICT, "会话含审核记录，请先撤回并清理正式报告");
    }

    public void requireNoPublicationsForWorkspace(WorkspaceIdentity identity) {
        int count = jdbc.queryForObject("SELECT COUNT(*) FROM report_publications WHERE workspace_id=?",
            Integer.class, identity.workspaceId());
        if (count > 0) throw new ResponseStatusException(HttpStatus.CONFLICT, "工作空间含审核记录，请先撤回并清理正式报告");
    }

    private Map<String, Object> lookup(String workspaceId, String id, boolean lock) {
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT * FROM report_publications WHERE id=? AND workspace_id=?" + (lock ? " FOR UPDATE" : ""), id, workspaceId);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "报告不存在");
        return rows.getFirst();
    }

    private Map<String, Object> detail(Map<String, Object> source) {
        Map<String, Object> row = new LinkedHashMap<>(source);
        List<Map<String, Object>> sources = new ArrayList<>();
        for (Map<String, Object> item : parseList(row.remove("sources_json"))) {
            Map<String, Object> evidence = new LinkedHashMap<>(item);
            evidence.put("original_available", originalAvailable(row, item));
            sources.add(evidence);
        }
        row.put("sources", sources);
        row.put("validation", parseMap(row.remove("validation_json")));
        return row;
    }

    private Object originalAvailable(Map<String, Object> publication, Map<String, Object> source) {
        String kind = String.valueOf(source.get("kind"));
        String workspaceId = String.valueOf(publication.get("workspace_id"));
        if ("local".equals(kind)) {
            return jdbc.queryForObject("""
                SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE c.id=? AND c.document_id=? AND c.user_id=? AND d.user_id=?
                AND d.status IN ('ready','rebuilding') AND d.hash=? AND d.index_version=? AND c.text=?""",
                Integer.class, source.get("chunk_id"), source.get("document_id"), workspaceId, workspaceId,
                source.get("document_hash"), source.get("index_version"), source.get("text")) > 0;
        }
        if ("attachment".equals(kind)) {
            return jdbc.queryForObject("""
                SELECT COUNT(*) FROM attachments WHERE id=? AND user_id=? AND status='ready'""",
                Integer.class, source.get("attachment_id"), workspaceId) > 0;
        }
        return null; // An external web page is not continuously verified.
    }

    private Map<String, Object> parseMap(Object raw) {
        try { return mapper.readValue(String.valueOf(raw), MAP); }
        catch (Exception error) { return Map.of(); }
    }

    private List<Map<String, Object>> parseList(Object raw) {
        try { return mapper.readValue(String.valueOf(raw), LIST); }
        catch (Exception error) { return List.of(); }
    }

    private static long number(Object value) {
        return value instanceof Number n ? n.longValue() : -1;
    }

    private static void requireStatus(Map<String, Object> row, String status) {
        if (!status.equals(row.get("status")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "报告状态已变化，请刷新后重试");
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    private void lockPublicationWorkspace(String workspaceId) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(("publication:" + workspaceId).getBytes(StandardCharsets.UTF_8));
            jdbc.query("SELECT pg_advisory_xact_lock(?)", result -> null, ByteBuffer.wrap(digest).getLong());
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }
}
