package com.deepresearch.business.service;

import static com.deepresearch.business.security.WorkspaceAccess.now;
import static com.deepresearch.business.security.WorkspaceAccess.uid;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import com.deepresearch.business.api.Requests;
import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class ResearchCommandService {
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final DeepResearchProperties properties;
    private final WorkspaceAccess access;
    private final RunViewService views;
    private final OutboxService outbox;

    public ResearchCommandService(JdbcTemplate jdbc, ObjectMapper mapper, DeepResearchProperties properties,
                                  WorkspaceAccess access, RunViewService views, OutboxService outbox) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.properties = properties;
        this.access = access;
        this.views = views;
        this.outbox = outbox;
    }

    @Transactional
    public Map<String, Object> create(WorkspaceIdentity identity, Requests.Run request) {
        access.require(identity, "admin", "researcher");
        lockQueueAdmission(identity.workspaceId());
        List<Map<String, Object>> previous = jdbc.queryForList(
            "SELECT * FROM runs WHERE user_id=? AND client_request_id=?", identity.workspaceId(), request.getClientRequestId());
        if (!previous.isEmpty()) {
            if (!identity.subject().equals(previous.getFirst().get("created_by")))
                throw new ResponseStatusException(HttpStatus.CONFLICT, "请求编号已被其他成员使用");
            verifyReplay(identity, request, previous.getFirst());
            return views.render(previous.getFirst());
        }
        if ("restricted".equals(request.getDataPolicy()) ||
            ("internal".equals(request.getDataPolicy()) && !properties.isAllowInternalModelProcessing())) {
            throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,
                "内部资料尚未获准由已配置模型处理；敏感资料禁止外发");
        }
        int waiting = jdbc.queryForObject("SELECT COUNT(*) FROM runs WHERE status IN ('queued','interrupted')", Integer.class);
        if (waiting >= properties.getMaxQueuedRuns()) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "待处理任务已满，请稍后提交");
        }
        Map<String, Object> limits = jdbc.queryForMap(
            "SELECT concurrent_run_limit FROM workspace_limits WHERE workspace_id=?", identity.workspaceId());
        int active = jdbc.queryForObject("""
            SELECT COUNT(*) FROM runs WHERE user_id=?
            AND status IN ('queued','running')""", Integer.class, identity.workspaceId());
        if (active >= ((Number) limits.get("concurrent_run_limit")).intValue()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "工作空间正在执行的研究已达到并发上限");
        }
        validateAttachments(identity, request.getAttachmentIds());
        String threadId = request.getThreadId() == null || request.getThreadId().isBlank()
            ? createThread(identity, request.normalizedTopic()).get("id").toString()
            : visibleThread(identity, request.getThreadId()).get("id").toString();
        String runId = uid();
        String stamp = now();
        jdbc.update("""
            INSERT INTO runs(
            id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id,created_by,data_policy,deadline_at)
            VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?)""", runId, identity.workspaceId(), threadId, request.normalizedTopic(),
            request.getMode(), stamp, stamp, request.getClientRequestId(), identity.subject(), request.getDataPolicy(),
            Instant.now().getEpochSecond() + properties.getMaxRunTotalSeconds());
        for (int index = 0; index < request.getAttachmentIds().size(); index++) {
            jdbc.update("UPDATE attachments SET run_id=?,position=? WHERE id=? AND run_id=''", runId, index,
                request.getAttachmentIds().get(index));
        }
        event(runId, "queued", Map.of("message", "研究任务已创建"));
        outbox.enqueue(runId, "research.schedule", "{\"resume\":false}");
        access.audit(identity, "research.create", "run", runId);
        return views.ownedRun(runId, identity.workspaceId());
    }

    @Transactional
    public Map<String, Object> cancel(WorkspaceIdentity identity, String runId) {
        access.require(identity, "admin", "researcher");
        lockWorkspaceAdmission(identity.workspaceId());
        views.visibleRun(runId, identity);
        int changed = jdbc.update("""
            UPDATE runs SET status='cancelled',updated_at=? WHERE id=? AND user_id=?
            AND status IN ('queued','running','interrupted')""", now(), runId, identity.workspaceId());
        if (changed > 0) {
            event(runId, "cancelled", Map.of("message", "任务已停止"));
            outbox.enqueue(runId, "research.abort", "{}");
            access.audit(identity, "research.cancel", "run", runId);
        }
        return views.ownedRun(runId, identity.workspaceId());
    }

    @Transactional
    public Map<String, Object> resume(WorkspaceIdentity identity, String runId) {
        access.require(identity, "admin", "researcher");
        lockQueueAdmission(identity.workspaceId());
        Map<String, Object> run = views.visibleRun(runId, identity);
        if (!Boolean.TRUE.equals(run.get("can_resume"))) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "该任务不可继续；请处理错误原因或缩小问题重新提交");
        }
        int waiting = jdbc.queryForObject("SELECT COUNT(*) FROM runs WHERE status IN ('queued','interrupted')", Integer.class);
        int alreadyWaiting = "interrupted".equals(run.get("status")) ? 1 : 0;
        if (waiting - alreadyWaiting >= properties.getMaxQueuedRuns()) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "待处理任务已满，请稍后继续");
        }
        int active = jdbc.queryForObject("""
            SELECT COUNT(*) FROM runs WHERE user_id=?
            AND status IN ('queued','running')""", Integer.class, identity.workspaceId());
        int limit = jdbc.queryForObject("SELECT concurrent_run_limit FROM workspace_limits WHERE workspace_id=?",
            Integer.class, identity.workspaceId());
        if (active >= limit) throw new ResponseStatusException(HttpStatus.CONFLICT, "工作空间正在执行的研究已达到并发上限");
        jdbc.update("UPDATE runs SET status='queued',updated_at=? WHERE id=? AND user_id=?", now(), runId, identity.workspaceId());
        event(runId, "queued", Map.of("message", "任务已提交恢复"));
        outbox.enqueue(runId, "research.resume", "{\"resume\":true}");
        access.audit(identity, "research.resume", "run", runId);
        return views.ownedRun(runId, identity.workspaceId());
    }

    @Transactional
    public Map<String, Object> createThread(WorkspaceIdentity identity, String title) {
        access.require(identity, "admin", "researcher");
        jdbc.query("SELECT pg_advisory_xact_lock(hashtext(?))", result -> null, "thread:" + identity.workspaceId());
        int index = 1;
        while (!jdbc.queryForList("SELECT id FROM threads WHERE user_id=? AND thread_key=?", identity.workspaceId(),
            "thread%02d".formatted(index)).isEmpty()) index++;
        Map<String, Object> thread = Map.of("id", uid(), "user_id", identity.workspaceId(),
            "title", title.strip().substring(0, Math.min(100, title.strip().length())),
            "thread_key", "thread%02d".formatted(index), "created_at", now());
        jdbc.update("INSERT INTO threads(id,user_id,title,thread_key,created_at,created_by) VALUES(?,?,?,?,?,?)",
            thread.get("id"), thread.get("user_id"), thread.get("title"), thread.get("thread_key"), thread.get("created_at"), identity.subject());
        return thread;
    }

    public Map<String, Object> resolveThread(String workspaceId, String reference) {
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT * FROM threads WHERE user_id=? AND (id=? OR thread_key=?)", workspaceId, reference, reference);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "会话不存在");
        return rows.getFirst();
    }

    public Map<String, Object> visibleThread(WorkspaceIdentity identity, String reference) {
        Map<String, Object> thread = resolveThread(identity.workspaceId(), reference);
        if (!identity.hasRole("admin") && !identity.subject().equals(thread.get("created_by")))
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "会话不存在");
        return thread;
    }

    private void verifyReplay(WorkspaceIdentity identity, Requests.Run request, Map<String, Object> previous) {
        List<String> attachments = jdbc.queryForList(
            "SELECT id FROM attachments WHERE run_id=? AND status='ready' ORDER BY position", String.class, previous.get("id"));
        boolean threadMatches = true;
        if (request.getThreadId() != null && !request.getThreadId().isBlank()) {
            threadMatches = Objects.equals(visibleThread(identity, request.getThreadId()).get("id"), previous.get("thread_id"));
        }
        if (!Objects.equals(previous.get("topic"), request.normalizedTopic())
            || !Objects.equals(previous.get("mode"), request.getMode())
            || !Objects.equals(previous.get("data_policy"), request.getDataPolicy())
            || !attachments.equals(request.getAttachmentIds()) || !threadMatches) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "同一个请求编号不能提交不同内容");
        }
    }

    private void validateAttachments(WorkspaceIdentity identity, List<String> attachmentIds) {
        for (String id : attachmentIds) {
            List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT id FROM attachments WHERE id=? AND user_id=?
                AND owner_subject=? AND status='ready' AND run_id=''""", id, identity.workspaceId(), identity.subject());
            if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY, "图片不存在、已使用或不可访问");
        }
    }

    private void event(String runId, String type, Map<String, Object> data) {
        try {
            jdbc.update("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                runId, type, mapper.writeValueAsString(data), now());
        } catch (JsonProcessingException error) {
            throw new IllegalStateException(error);
        }
    }

    private void lockQueueAdmission(String workspaceId) {
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", result -> null,
            "research-global-queue-admission");
        lockWorkspaceAdmission(workspaceId);
    }

    private void lockWorkspaceAdmission(String workspaceId) {
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", result -> null,
            "research-admission:" + workspaceId);
    }
}
