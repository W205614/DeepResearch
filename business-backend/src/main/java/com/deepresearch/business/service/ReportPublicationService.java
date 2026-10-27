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
import java.util.Objects;
import java.util.Base64;
import java.util.Set;
import java.util.HashSet;
import com.deepresearch.business.api.Requests;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class ReportPublicationService {
    private static final String VERIFICATION_CONTRACT = "verification-v3-claim-span";
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {};
    private static final TypeReference<List<Map<String, Object>>> LIST = new TypeReference<>() {};
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final WorkspaceAccess access;
    private final ResearchCommandService commands;

    public ReportPublicationService(JdbcTemplate jdbc, ObjectMapper mapper, WorkspaceAccess access,
                                    ResearchCommandService commands) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.access = access;
        this.commands = commands;
    }

    @Transactional
    public Map<String, Object> submit(WorkspaceIdentity identity, String runId) {
        access.require(identity, "admin", "researcher");
        requireCurrentRole(identity, "admin", "researcher");
        lockPublicationWorkspace(identity.workspaceId());
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT * FROM runs WHERE id=? AND user_id=? FOR UPDATE", runId, identity.workspaceId());
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在");
        Map<String, Object> run = rows.getFirst();
        if (!identity.subject().equals(run.get("created_by")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "只能提交本人创建的研究报告");
        DocumentAccessService.requireSources(jdbc, mapper, identity, run.get("sources"));
        List<Map<String, Object>> existing = jdbc.queryForList(
            "SELECT * FROM report_publications WHERE source_run_id=?", runId);
        if (!existing.isEmpty()) return detail(existing.getFirst());
        if (Boolean.TRUE.equals(run.get("report_submitted")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "该任务已经送审；请新建研究任务后重新提交");
        Map<String, Object> validation = parseMap(run.get("validation"));
        requireCurrentVerification(validation);
        List<Map<String, Object>> sources = parseList(run.get("sources"));
        String report = String.valueOf(run.get("report") == null ? "" : run.get("report"));
        if (report.length() > 2_000_000 || sources.size() > 1000)
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "报告或引用超过交付上限，请缩小研究范围");
        boolean hasQuotedEvidence = sources.stream().anyMatch(source -> {
            Object id = source.get("id"), text = source.get("text");
            return id instanceof String key && !key.isBlank()
                && text instanceof String excerpt && !excerpt.isBlank()
                && report.contains("[" + key + "]");
        });
        String quality = String.valueOf(validation.get("quality"));
        long checked = number(validation.get("checked_claims"));
        long supported = number(validation.get("supported_claims"));
        if (!"completed".equals(run.get("status")) || !List.of("complete", "partial").contains(quality)
            || Boolean.TRUE.equals(validation.get("verification_pending"))
            || report.isBlank() || !hasQuotedEvidence || supported < 1 || checked < supported
            || ("complete".equals(quality) && supported != checked)) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                "只有保留已核验证据、并明确标示未回答内容的研究报告可提交审核");
        }
        String sourceJson = String.valueOf(run.get("sources"));
        String validationJson = String.valueOf(run.get("validation"));
        long corpus = "public".equals(run.get("data_policy")) ? -1 : corpusRevision(identity.workspaceId(), true);
        List<Map<String, Object>> artifactRows = jdbc.queryForList("SELECT * FROM run_artifacts WHERE run_id=? AND workspace_id=?",
            runId, identity.workspaceId());
        Map<String, Object> artifacts = artifactRows.isEmpty() ? Map.of() : artifactRows.getFirst();
        if (!artifacts.isEmpty() && number(artifacts.get("attempt_count")) != number(run.get("attempt_count")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "研究执行代次与证据产物不一致，请重新研究");
        if (!artifacts.isEmpty() && number(artifacts.get("corpus_revision")) != corpus)
            throw new ResponseStatusException(HttpStatus.CONFLICT, "资料版本已变化，请重新研究后送审");
        requireSourcesCurrent(identity.workspaceId(), sources);
        Map<String, Object> structured = new LinkedHashMap<>(parseMap(artifacts.get("requirements_json")));
        if (!artifacts.isEmpty()) structured.put("claims", parseList(artifacts.get("claims_json")));
        String artifactsJson = json(structured);
        List<Map<String, Object>> jobs = jdbc.queryForList("SELECT * FROM report_revision_jobs WHERE target_run_id=? FOR UPDATE", runId);
        String parentId = null;
        String seriesId = uid();
        int revisionNo = 1;
        if (!jobs.isEmpty()) {
            Map<String, Object> job = jobs.getFirst();
            if (number(job.get("target_corpus_revision")) != corpus)
                throw new ResponseStatusException(HttpStatus.CONFLICT, "修订目标资料已过期，请重新发起修订");
            parentId = String.valueOf(job.get("source_report_id"));
            Map<String, Object> parent = lookup(identity.workspaceId(), parentId, true);
            if (!Objects.equals(parent.get("content_sha256"), job.get("source_content_sha256")))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "原报告指纹已变化，请重新发起修订");
            seriesId = String.valueOf(parent.get("series_id"));
            Map<String, Object> series = jdbc.queryForMap("SELECT * FROM report_series WHERE id=? FOR UPDATE", seriesId);
            if (!Objects.equals(parentId, series.get("current_report_id")))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "当前正式版本已变化，请从最新版本修订");
            revisionNo = jdbc.queryForObject("SELECT COALESCE(MAX(revision_no),0)+1 FROM report_publications WHERE series_id=?", Integer.class, seriesId);
        } else {
            jdbc.update("INSERT INTO report_series(id,workspace_id,created_at,updated_at) VALUES(?,?,?,?)",
                seriesId, identity.workspaceId(), now(), now());
        }
        String digest = sha256(report + "\n" + sourceJson + "\n" + validationJson + "\n" + artifactsJson);
        String id = uid();
        jdbc.update("""
            INSERT INTO report_publications(id,workspace_id,source_run_id,author_subject,topic,
                report_markdown,sources_json,validation_json,content_sha256,status,submitted_at,
                series_id,parent_report_id,revision_no,artifacts_json,corpus_revision,evidence_status)
            VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,'current')""", id, identity.workspaceId(), runId, identity.subject(),
            String.valueOf(run.get("topic")), report, sourceJson, validationJson, digest, now(),
            seriesId, parentId, revisionNo, artifactsJson, corpus);
        if (!jobs.isEmpty()) jdbc.update("UPDATE report_revision_jobs SET status='submitted',updated_at=? WHERE target_run_id=?", now(), runId);
        jdbc.update("UPDATE runs SET report_submitted=TRUE WHERE id=?", runId);
        access.audit(identity, "report.submit", "report", id);
        return detail(lookup(identity.workspaceId(), id, false));
    }

    @Transactional
    public Map<String, Object> approve(WorkspaceIdentity identity, String reportId, String reason) {
        access.require(identity, "admin");
        requireCurrentRole(identity, "admin");
        lockPublicationWorkspace(identity.workspaceId());
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, true);
        if (identity.subject().equals(row.get("author_subject")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "报告作者不能审核自己的报告");
        requireStatus(row, "pending");
        requireCurrentVerification(parseMap(row.get("validation_json")));
        long corpus = corpusRevision(identity.workspaceId(), true);
        if (number(row.get("corpus_revision")) >= 0 && number(row.get("corpus_revision")) != corpus)
            throw new ResponseStatusException(HttpStatus.CONFLICT, "资料已变化，请修订后重新审核");
        requireSourcesCurrent(identity.workspaceId(), parseList(row.get("sources_json")));
        String expectedDigest = sha256(row.get("report_markdown") + "\n" + row.get("sources_json") + "\n"
            + row.get("validation_json") + "\n" + row.get("artifacts_json"));
        if (!expectedDigest.equals(row.get("content_sha256")) && parseMap(row.get("artifacts_json")).isEmpty())
            expectedDigest = sha256(row.get("report_markdown") + "\n" + row.get("sources_json") + "\n" + row.get("validation_json"));
        if (!expectedDigest.equals(row.get("content_sha256")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "审核快照校验失败");
        if (row.get("parent_report_id") != null) {
            Map<String, Object> parent = lookup(identity.workspaceId(), String.valueOf(row.get("parent_report_id")), false);
            List<Map<String, Object>> jobs = jdbc.queryForList("SELECT source_content_sha256,target_corpus_revision FROM report_revision_jobs WHERE target_run_id=? AND workspace_id=?",
                row.get("source_run_id"), identity.workspaceId());
            if (jobs.isEmpty() || !Objects.equals(parent.get("content_sha256"), jobs.getFirst().get("source_content_sha256"))
                || number(row.get("corpus_revision")) != number(jobs.getFirst().get("target_corpus_revision")))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "修订依据已变化，请重新发起修订");
        }
        if (row.get("series_id") != null) {
            Map<String, Object> series = jdbc.queryForMap("SELECT * FROM report_series WHERE id=? FOR UPDATE", row.get("series_id"));
            if (!Objects.equals(row.get("parent_report_id"), series.get("current_report_id")))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "当前正式版本已变化，旧修订不能发布");
            jdbc.update("UPDATE report_series SET current_report_id=?,updated_at=? WHERE id=?",
                reportId, now(), row.get("series_id"));
        }
        String note = reason == null ? "" : reason.strip();
        if (note.length() > 1000 || ("partial".equals(parseMap(row.get("validation_json")).get("quality")) && note.isBlank()))
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "部分结果须填写批准范围与未回答问题的审核说明");
        jdbc.update("UPDATE report_publications SET status='published',reviewed_by=?,reviewed_at=?,review_reason=? WHERE id=?",
            identity.subject(), now(), note, reportId);
        access.audit(identity, "report.approve", "report", reportId);
        jdbc.update("UPDATE report_revision_jobs SET status='published',updated_at=? WHERE target_run_id=?", now(), row.get("source_run_id"));
        return detail(lookup(identity.workspaceId(), reportId, false));
    }

    @Transactional
    public Map<String, Object> reject(WorkspaceIdentity identity, String reportId, String reason) {
        access.require(identity, "admin");
        requireCurrentRole(identity, "admin");
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
        requireCurrentRole(identity, "admin");
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, true);
        requireStatus(row, "published");
        jdbc.update("UPDATE report_publications SET status='withdrawn',withdrawn_by=?,withdrawn_at=?,withdrawal_reason=? WHERE id=?",
            identity.subject(), now(), reason.strip(), reportId);
        jdbc.update("UPDATE report_series SET current_report_id=NULL,updated_at=? WHERE id=? AND current_report_id=?",
            now(), row.get("series_id"), reportId);
        access.audit(identity, "report.withdraw", "report", reportId);
        return detail(lookup(identity.workspaceId(), reportId, false));
    }

    @Transactional
    public void delete(WorkspaceIdentity identity, String reportId) {
        access.require(identity, "admin");
        requireCurrentRole(identity, "admin");
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, true);
        if (!List.of("rejected", "withdrawn").contains(row.get("status")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只有已驳回或已撤回的报告可以清理");
        int dependents = jdbc.queryForObject("""
            SELECT (SELECT COUNT(*) FROM report_publications WHERE parent_report_id=?)
                 + (SELECT COUNT(*) FROM report_revision_jobs WHERE source_report_id=?)""", Integer.class, reportId, reportId);
        if (dependents > 0)
            throw new ResponseStatusException(HttpStatus.CONFLICT, "报告已有修订依赖，须保留审计历史");
        jdbc.update("DELETE FROM report_impacts WHERE report_id=?", reportId);
        jdbc.update("DELETE FROM report_publications WHERE id=?", reportId);
        access.audit(identity, "report.delete", "report", reportId);
    }

    public List<Map<String, Object>> published(WorkspaceIdentity identity) {
        return page(identity, "published", null, 20).rows();
    }

    public List<Map<String, Object>> pending(WorkspaceIdentity identity) {
        return page(identity, "pending", null, 20).rows();
    }

    public List<Map<String, Object>> archived(WorkspaceIdentity identity) {
        return page(identity, "archived", null, 20).rows();
    }

    public Map<String, Object> get(WorkspaceIdentity identity, String reportId) {
        Map<String, Object> row = lookup(identity.workspaceId(), reportId, false);
        if (!"published".equals(row.get("status")) && !identity.hasRole("admin")
            && !identity.subject().equals(row.get("author_subject")))
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "报告不存在");
        DocumentAccessService.requireSources(jdbc, mapper, identity, row.get("sources_json"));
        return detail(row);
    }

    public record Page(List<Map<String, Object>> rows, String nextCursor) {}

    public Page page(WorkspaceIdentity identity, String category, String cursor, int limit) {
        if (!"published".equals(category)) access.require(identity, "admin");
        if (limit < 1 || limit > 100) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "每页数量须为 1 到 100");
        String before = "", beforeTime = "";
        if (cursor != null && !cursor.isBlank()) {
            try {
                String[] parts = new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8).split("\n", -1);
                if (parts.length != 2) throw new IllegalArgumentException();
                beforeTime = parts[0]; before = parts[1];
                java.time.Instant.parse(beforeTime);
            }
            catch (IllegalArgumentException error) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "分页游标无效"); }
            catch (java.time.format.DateTimeParseException error) { throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "分页游标无效"); }
            if (!before.matches("[a-zA-Z0-9_-]{1,80}")) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "分页游标无效");
        }
        String statuses = switch (category) {
            case "published" -> "('published')";
            case "pending" -> "('pending')";
            case "archived" -> "('rejected','withdrawn')";
            default -> throw new IllegalArgumentException("未知报告分类");
        };
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT id,source_run_id,topic,author_subject,content_sha256,submitted_at,reviewed_by,
                reviewed_at,status,series_id,parent_report_id,revision_no,corpus_revision,evidence_status,sources_json,
                (SELECT s.current_report_id=report_publications.id FROM report_series s WHERE s.id=report_publications.series_id) AS is_current,
                validation_json::jsonb->>'quality' AS quality
            FROM report_publications WHERE workspace_id=? AND status IN """ + statuses
            + " AND (?='' OR (submitted_at,id)<(?,?)) ORDER BY submitted_at DESC,id DESC LIMIT ?",
            identity.workspaceId(), before, beforeTime, before, limit + 1);
        String next = rows.size() > limit ? Base64.getUrlEncoder().withoutPadding().encodeToString(
            (rows.get(limit - 1).get("submitted_at") + "\n" + rows.get(limit - 1).get("id")).getBytes(StandardCharsets.UTF_8)) : null;
        List<Map<String, Object>> visible = new ArrayList<>(rows.subList(0, Math.min(limit, rows.size())).stream()
            .filter(row -> DocumentAccessService.sourcesAllowed(jdbc,mapper,identity,row.get("sources_json"))).toList());
        visible.forEach(row -> row.remove("sources_json"));
        long current = corpusRevision(identity.workspaceId(), false);
        visible.forEach(row -> { if (number(row.get("corpus_revision")) >= 0 && number(row.get("corpus_revision")) != current
            && !List.of("needs_review", "undetermined").contains(row.get("evidence_status"))) row.put("evidence_status", "check_pending"); });
        return new Page(visible, next);
    }

    public Map<String, Object> impacts(WorkspaceIdentity identity, String reportId) {
        Map<String, Object> report = get(identity, reportId);
        long current = number(report.get("corpus_revision")) < 0 ? -1 : corpusRevision(identity.workspaceId(), false);
        List<Map<String, Object>> assessments = jdbc.queryForList("SELECT * FROM report_impacts WHERE report_id=? ORDER BY target_revision DESC LIMIT 20", reportId);
        for (Map<String, Object> assessment : assessments) {
            assessment.put("reasons", parseValue(assessment.remove("reasons_json"), List.of()));
            assessment.put("affected_claims", parseValue(assessment.remove("affected_claims_json"), List.of()));
            assessment.put("diff", parseValue(assessment.remove("diff_json"), Map.of()));
        }
        List<Map<String, Object>> jobs = jdbc.queryForList("""
            SELECT j.id,j.target_run_id,CASE WHEN j.status='queued' THEN r.status ELSE j.status END AS status,
                j.target_corpus_revision,j.created_at FROM report_revision_jobs j JOIN runs r ON r.id=j.target_run_id
            WHERE j.source_report_id=? AND j.workspace_id=? AND (j.requested_by=? OR ?)
            ORDER BY j.created_at DESC LIMIT 20""", reportId, identity.workspaceId(), identity.subject(), identity.hasRole("admin"));
        return Map.of("report_id", reportId, "corpus_revision", report.get("corpus_revision"),
            "current_corpus_revision", current, "evidence_status", report.get("evidence_status"),
            "impacts", assessments, "revision_jobs", jobs);
    }

    @Transactional
    public Map<String, Object> revise(WorkspaceIdentity identity, String reportId, Requests.ReportRevision request) {
        access.require(identity, "admin", "researcher");
        requireCurrentRole(identity, "admin", "researcher");
        lockPublicationWorkspace(identity.workspaceId());
        Map<String, Object> source = lookup(identity.workspaceId(), reportId, true);
        DocumentAccessService.requireSources(jdbc, mapper, identity, source.get("sources_json"));
        boolean currentAdmin = "admin".equals(jdbc.queryForObject("SELECT role FROM memberships WHERE workspace_id=? AND subject=?", String.class, identity.workspaceId(), identity.subject()));
        if (!currentAdmin && !identity.subject().equals(source.get("author_subject")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "只有报告作者或管理员可以修订");
        List<Map<String, Object>> previous = jdbc.queryForList("""
            SELECT * FROM report_revision_jobs WHERE workspace_id=? AND requested_by=? AND idempotency_key=?""",
            identity.workspaceId(), identity.subject(), request.client_request_id());
        if (!previous.isEmpty()) {
            Map<String, Object> job = previous.getFirst();
            if (!reportId.equals(job.get("source_report_id"))
                || !request.expected_content_sha256().equals(job.get("source_content_sha256"))
                || request.expected_corpus_revision() != number(job.get("target_corpus_revision")))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "同一个请求编号不能修订不同内容");
            return revisionResult(job);
        }
        if (!"published".equals(source.get("status")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只能从当前已发布报告发起修订");
        long corpus = number(source.get("corpus_revision")) < 0 ? -1 : corpusRevision(identity.workspaceId(), true);
        if (request.expected_corpus_revision() != corpus || !request.expected_content_sha256().equals(source.get("content_sha256")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "报告或资料版本已变化，请刷新后修订");
        if (source.get("series_id") == null) {
            String seriesId = uid();
            jdbc.update("INSERT INTO report_series(id,workspace_id,current_report_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                seriesId, identity.workspaceId(), reportId, now(), now());
            jdbc.update("UPDATE report_publications SET series_id=?,revision_no=1 WHERE id=?", seriesId, reportId);
            source.put("series_id", seriesId);
        }
        Object current = jdbc.queryForMap("SELECT current_report_id FROM report_series WHERE id=? FOR UPDATE", source.get("series_id")).get("current_report_id");
        if (!reportId.equals(current)) throw new ResponseStatusException(HttpStatus.CONFLICT, "请从最新正式版本发起修订");
        Map<String, Object> sourceRun = jdbc.queryForMap("SELECT topic,mode,data_policy FROM runs WHERE id=?", source.get("source_run_id"));
        Requests.Run run = new Requests.Run();
        run.setTopic(String.valueOf(sourceRun.get("topic")));
        run.setMode(String.valueOf(sourceRun.get("mode")));
        run.setDataPolicy(String.valueOf(sourceRun.get("data_policy")));
        // The revision job owns replay identity; reserve a fresh run request key so a
        // normal research request cannot preoccupy a predictable internal namespace.
        run.setClientRequestId("revision-" + uid());
        ResearchIntakePolicy.authorizeRevision(jdbc,mapper,identity,run);
        Map<String, Object> created = commands.create(identity, run);
        String id = uid();
        jdbc.update("""
            INSERT INTO report_revision_jobs(id,workspace_id,source_report_id,target_run_id,idempotency_key,
              requested_by,target_corpus_revision,source_content_sha256,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,'queued',?,?)""", id, identity.workspaceId(), reportId, created.get("id"),
            request.client_request_id(), identity.subject(), corpus, source.get("content_sha256"), now(), now());
        access.audit(identity, "report.revise", "report", reportId);
        return Map.of("revision_job_id", id, "run_id", created.get("id"), "status", "queued");
    }

    public Map<String, Object> series(WorkspaceIdentity identity, String seriesId) {
        List<Map<String, Object>> series = jdbc.queryForList("SELECT * FROM report_series WHERE id=? AND workspace_id=?", seriesId, identity.workspaceId());
        if (series.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "报告系列不存在");
        List<Map<String, Object>> reports = jdbc.queryForList("""
            SELECT id,source_run_id,topic,status,author_subject,revision_no,parent_report_id,content_sha256,
              corpus_revision,evidence_status,submitted_at,reviewed_at,sources_json
            FROM report_publications WHERE series_id=? AND workspace_id=?
              AND (status='published' OR author_subject=? OR ?)
            ORDER BY revision_no DESC LIMIT 100""", seriesId, identity.workspaceId(), identity.subject(), identity.hasRole("admin"))
            .stream().filter(row -> DocumentAccessService.sourcesAllowed(jdbc, mapper, identity, row.get("sources_json"))).toList();
        if (reports.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "报告系列不存在");
        Map<String, Object> result = new LinkedHashMap<>(series.getFirst());
        if(reports.stream().noneMatch(report -> Objects.equals(report.get("id"), result.get("current_report_id"))))
            result.put("current_report_id", null);
        long currentCorpus = corpusRevision(identity.workspaceId(), false);
        for (Map<String, Object> report : reports) {
            report.remove("sources_json");
            report.put("is_current", Objects.equals(report.get("id"), result.get("current_report_id")));
            if (number(report.get("corpus_revision")) >= 0 && number(report.get("corpus_revision")) != currentCorpus
                && !List.of("needs_review", "undetermined").contains(report.get("evidence_status")))
                report.put("evidence_status", "check_pending");
        }
        result.put("reports", reports);
        return result;
    }

    public Map<String, Object> diff(WorkspaceIdentity identity, String reportId, String against) {
        Map<String, Object> after = get(identity, reportId), before = get(identity, against);
        if (after.get("series_id") == null || !Objects.equals(after.get("series_id"), before.get("series_id")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只能比较同一系列的报告");
        Map<String, Object> oldClaims = claimsById(before.get("artifacts"));
        Map<String, Object> newClaims = claimsById(after.get("artifacts"));
        List<Object> added = new ArrayList<>(), removed = new ArrayList<>();
        List<Map<String, Object>> changes = new ArrayList<>();
        newClaims.forEach((key, value) -> {
            if (!oldClaims.containsKey(key)) added.add(value);
            else {
                Object old = oldClaims.get(key);
                if (!logicalClaim(old).equals(logicalClaim(value)))
                    changes.add(Map.of("kind", "changed", "old", old, "new", value));
                else if (!evidenceIdentity(old).equals(evidenceIdentity(value)))
                    changes.add(Map.of("kind", "evidence_changed", "old", old, "new", value));
            }
        });
        oldClaims.forEach((key, value) -> { if (!newClaims.containsKey(key)) removed.add(value); });
        added.forEach(value -> { Map<String, Object> entry = new LinkedHashMap<>(); entry.put("kind", "added"); entry.put("old", null); entry.put("new", value); changes.add(entry); });
        removed.forEach(value -> { Map<String, Object> entry = new LinkedHashMap<>(); entry.put("kind", "removed"); entry.put("old", value); entry.put("new", null); changes.add(entry); });
        return Map.of("report_id", reportId, "against", against, "before_report", before.get("report_markdown"),
            "after_report", after.get("report_markdown"), "changes", changes,
            "legacy", oldClaims.isEmpty() || newClaims.isEmpty());
    }

    private Map<String, Object> logicalClaim(Object raw) {
        if (!(raw instanceof Map<?, ?> claim)) return Map.of();
        return Map.of("text", String.valueOf(claim.get("text")).strip().replace("\r\n", "\n"),
            "type", String.valueOf(claim.containsKey("type") ? claim.get("type") : claim.get("kind")));
    }

    private List<String> evidenceIdentity(Object raw) {
        if (!(raw instanceof Map<?, ?> claim)) return List.of();
        List<String> identities = new ArrayList<>();
        if (claim.get("evidence") instanceof List<?> sources && !sources.isEmpty()) {
            for (Object source : sources) {
                if (!(source instanceof Map<?, ?> evidence)) continue;
                Map<String, Object> stable = new LinkedHashMap<>();
                for (String field : List.of("kind", "document_id", "index_version", "document_hash", "hash", "locator", "text", "url", "attachment_id")) {
                    Object value = evidence.get(field);
                    if (value != null && !(value instanceof String text && text.isBlank())) stable.put(field, value);
                }
                identities.add(json(stable));
            }
        } else if (claim.get("source_ids") instanceof List<?> ids) {
            // Older artifacts lack source snapshots. A changed citation set must
            // remain visible rather than being silently treated as unchanged.
            ids.forEach(id -> identities.add(String.valueOf(id)));
        }
        return identities.stream().distinct().sorted().toList();
    }

    private Map<String, Object> claimsById(Object artifacts) {
        Map<String, Object> result = new LinkedHashMap<>();
        if (artifacts instanceof Map<?, ?> map && map.get("claims") instanceof List<?> claims)
            for (Object claim : claims) if (claim instanceof Map<?, ?> entry && entry.get("id") != null)
                result.put(String.valueOf(entry.get("id")), claim);
        return result;
    }

    private Map<String, Object> revisionResult(Map<String, Object> job) {
        return Map.of("revision_job_id", job.get("id"), "run_id", job.get("target_run_id"), "status", job.get("status"));
    }

    public void requireNoPublicationsForThread(WorkspaceIdentity identity, String threadId) {
        int count = jdbc.queryForObject("""
            SELECT COUNT(*) FROM report_publications p JOIN runs r ON r.id=p.source_run_id
            WHERE p.workspace_id=? AND r.thread_id=?""", Integer.class, identity.workspaceId(), threadId);
        if (count > 0) throw new ResponseStatusException(HttpStatus.CONFLICT, "会话含审核记录，请先撤回并清理正式报告");
        int revisions = jdbc.queryForObject("""
            SELECT COUNT(*) FROM report_revision_jobs j JOIN runs r ON r.id=j.target_run_id
            WHERE j.workspace_id=? AND r.thread_id=?""", Integer.class, identity.workspaceId(), threadId);
        if (revisions > 0) throw new ResponseStatusException(HttpStatus.CONFLICT, "会话含报告修订记录，须保留修订审计历史");
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

    private void requireCurrentRole(WorkspaceIdentity identity, String... roles) {
        List<Map<String, Object>> current = jdbc.queryForList(
            "SELECT role FROM memberships WHERE workspace_id=? AND subject=? FOR SHARE",
            identity.workspaceId(), identity.subject());
        if (current.isEmpty() || !List.of(roles).contains(current.getFirst().get("role")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "当前工作空间权限已撤销");
    }

    private Map<String, Object> detail(Map<String, Object> source) {
        Map<String, Object> row = new LinkedHashMap<>(source);
        List<Map<String, Object>> sources = new ArrayList<>();
        List<Map<String, Object>> snapshots = parseList(row.remove("sources_json"));
        Map<String, Boolean> available = availability(String.valueOf(row.get("workspace_id")), snapshots);
        for (Map<String, Object> item : snapshots) {
            Map<String, Object> evidence = new LinkedHashMap<>(item);
            evidence.put("original_available", available.get(String.valueOf(item.get("id"))));
            sources.add(evidence);
        }
        row.put("sources", sources);
        row.put("validation", parseMap(row.remove("validation_json")));
        row.put("artifacts", parseMap(row.remove("artifacts_json")));
        long current = number(row.get("corpus_revision")) < 0 ? -1 : corpusRevision(String.valueOf(row.get("workspace_id")), false);
        row.put("current_corpus_revision", current);
        List<String> publishedHead = row.get("series_id") == null ? List.of() : jdbc.queryForList(
            "SELECT current_report_id FROM report_series WHERE id=? AND workspace_id=?", String.class, row.get("series_id"), row.get("workspace_id"));
        row.put("is_current", row.get("series_id") == null ? "published".equals(row.get("status"))
            : !publishedHead.isEmpty() && Objects.equals(row.get("id"), publishedHead.getFirst()));
        if (number(row.get("corpus_revision")) != current && !List.of("needs_review", "undetermined").contains(row.get("evidence_status"))) row.put("evidence_status", "check_pending");
        return row;
    }

    private void requireSourcesCurrent(String workspaceId, List<Map<String, Object>> sources) {
        for (Boolean available : availability(workspaceId, sources).values()) {
            if (Boolean.FALSE.equals(available))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "引用资料已经更新、删除或失效，请重新取证");
        }
    }

    private Map<String, Boolean> availability(String workspaceId, List<Map<String, Object>> sources) {
        Map<String, Boolean> result = new LinkedHashMap<>();
        List<Object> chunks = new ArrayList<>(), attachments = new ArrayList<>();
        sources.forEach(source -> {
            if ("local".equals(source.get("kind"))) chunks.add(String.valueOf(source.get("chunk_id")));
            if ("attachment".equals(source.get("kind"))) attachments.add(String.valueOf(source.get("attachment_id")));
        });
        Map<String, Map<String, Object>> current = new LinkedHashMap<>();
        if (!chunks.isEmpty()) {
            List<Object> args = new ArrayList<>(List.of(workspaceId, workspaceId)); args.addAll(chunks);
            String placeholders = String.join(",", java.util.Collections.nCopies(chunks.size(), "?"));
            jdbc.queryForList("""
                SELECT c.id,c.document_id,c.text,d.hash,d.index_version FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE c.user_id=? AND d.user_id=? AND d.status IN ('ready','rebuilding') AND c.id IN (""" + placeholders + ")", args.toArray())
                .forEach(row -> current.put(String.valueOf(row.get("id")), row));
        }
        Set<String> readyAttachments = new HashSet<>();
        if (!attachments.isEmpty()) {
            List<Object> args = new ArrayList<>(List.of(workspaceId)); args.addAll(attachments);
            String placeholders = String.join(",", java.util.Collections.nCopies(attachments.size(), "?"));
            readyAttachments.addAll(jdbc.queryForList("SELECT id FROM attachments WHERE user_id=? AND status='ready' AND id IN (" + placeholders + ")", String.class, args.toArray()));
        }
        for (Map<String, Object> source : sources) {
            if ("local".equals(source.get("kind"))) {
                Map<String, Object> live = current.get(String.valueOf(source.get("chunk_id")));
                result.put(String.valueOf(source.get("id")), live != null
                    && Objects.equals(live.get("document_id"), source.get("document_id"))
                    && Objects.equals(live.get("hash"), source.get("document_hash"))
                    && number(live.get("index_version")) == number(source.get("index_version"))
                    && Objects.equals(live.get("text"), source.get("text")));
            } else if ("attachment".equals(source.get("kind"))) {
                result.put(String.valueOf(source.get("id")), readyAttachments.contains(String.valueOf(source.get("attachment_id"))));
            }
        }
        return result;
    }

    private long corpusRevision(String workspaceId, boolean lock) {
        if (lock) jdbc.update("INSERT INTO workspace_corpus_revisions(workspace_id,revision) VALUES(?,0) ON CONFLICT DO NOTHING", workspaceId);
        List<Long> revisions = jdbc.queryForList("SELECT revision FROM workspace_corpus_revisions WHERE workspace_id=?" + (lock ? " FOR SHARE" : ""), Long.class, workspaceId);
        return revisions.isEmpty() ? 0 : revisions.getFirst();
    }

    private String json(Object value) {
        try { return mapper.writeValueAsString(value); }
        catch (Exception error) { throw new IllegalStateException(error); }
    }

    private Object parseValue(Object raw, Object fallback) {
        try { Object parsed = mapper.readValue(String.valueOf(raw), Object.class); return parsed == null ? fallback : parsed; }
        catch (Exception error) { return fallback; }
    }

    private Map<String, Object> parseMap(Object raw) {
        try { Map<String, Object> parsed = mapper.readValue(String.valueOf(raw), MAP); return parsed == null ? Map.of() : parsed; }
        catch (Exception error) { return Map.of(); }
    }

    private List<Map<String, Object>> parseList(Object raw) {
        try { List<Map<String, Object>> parsed = mapper.readValue(String.valueOf(raw), LIST); return parsed == null ? List.of() : parsed; }
        catch (Exception error) { return List.of(); }
    }

    private static long number(Object value) {
        return value instanceof Number n ? n.longValue() : -1;
    }

    private static void requireCurrentVerification(Map<String, Object> validation) {
        if (!VERIFICATION_CONTRACT.equals(validation.get("verification_contract")))
            throw new ResponseStatusException(HttpStatus.CONFLICT, "该报告未通过当前原文核验合同，请新建研究后重新送审");
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
