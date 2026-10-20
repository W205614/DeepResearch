package com.deepresearch.business.api;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.AgentClient;
import com.deepresearch.business.service.ResearchCommandService;
import com.deepresearch.business.service.RunViewService;
import com.deepresearch.business.service.ReportPublicationService;
import com.deepresearch.business.service.PageCursor;
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
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;
import java.io.OutputStreamWriter;

@RestController
public class ThreadController {
    private final JdbcTemplate jdbc;
    private final WorkspaceAccess access;
    private final ResearchCommandService research;
    private final RunViewService views;
    private final AgentClient agent;
    private final ReportPublicationService publications;

    public ThreadController(JdbcTemplate jdbc, WorkspaceAccess access, ResearchCommandService research,
                            RunViewService views, AgentClient agent, ReportPublicationService publications) {
        this.jdbc = jdbc;
        this.access = access;
        this.research = research;
        this.views = views;
        this.agent = agent;
        this.publications = publications;
    }

    @PostMapping("/api/threads")
    ResponseEntity<Map<String, Object>> create(@AuthenticationPrincipal Jwt jwt,
                                               @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                               @Valid @RequestBody Requests.Thread body) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        return ResponseEntity.status(201).body(research.createThread(identity, body.title()));
    }

    @GetMapping("/api/threads")
    ResponseEntity<List<Map<String, Object>>> list(@AuthenticationPrincipal Jwt jwt,
                                   @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                   @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") int limit) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        String[] keys = PageCursor.decode(cursor, limit);
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT * FROM threads WHERE user_id=? AND (? OR created_by=?)
            AND (?='' OR (created_at,id)<(?,?)) ORDER BY created_at DESC,id DESC LIMIT ?""",
            identity.workspaceId(), identity.hasRole("admin"), identity.subject(), keys[1], keys[0], keys[1], limit + 1);
        String next = rows.size() > limit ? PageCursor.encode(rows.get(limit - 1)) : null;
        return page(new ReportPublicationService.Page(rows.subList(0, Math.min(limit, rows.size())), next));
    }

    @PatchMapping("/api/threads/{threadId}")
    @Transactional
    Map<String, Boolean> rename(@PathVariable String threadId, @AuthenticationPrincipal Jwt jwt,
                                @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                @Valid @RequestBody Requests.Thread body) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        access.require(identity, "admin", "researcher");
        Map<String, Object> thread = research.visibleThread(identity, threadId);
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
        Map<String, Object> thread = research.visibleThread(identity, threadId);
        publications.requireNoPublicationsForThread(identity, String.valueOf(thread.get("id")));
        return forwarded(agent.forward(request, new byte[0]));
    }

    @GetMapping("/api/threads/{threadId}/runs")
    ResponseEntity<List<Map<String, Object>>> runs(@PathVariable String threadId, @AuthenticationPrincipal Jwt jwt,
                                   @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                   @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") int limit) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        Map<String, Object> thread = research.visibleThread(identity, threadId);
        return page(views.page(String.valueOf(thread.get("id")), identity, cursor, limit));
    }

    @GetMapping("/api/threads/{threadId}/report")
    ResponseEntity<StreamingResponseBody> report(@PathVariable String threadId, @AuthenticationPrincipal Jwt jwt,
                                  @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        Map<String, Object> thread = research.visibleThread(identity, threadId);
        List<Map<String, Object>> upperRows = jdbc.queryForList("""
            SELECT created_at,id FROM runs WHERE thread_id=? AND user_id=? AND (? OR created_by=?)
            ORDER BY created_at DESC,id DESC LIMIT 1""", thread.get("id"), identity.workspaceId(), identity.hasRole("admin"), identity.subject());
        String upperTime = upperRows.isEmpty() ? "" : String.valueOf(upperRows.getFirst().get("created_at"));
        String upperId = upperRows.isEmpty() ? "" : String.valueOf(upperRows.getFirst().get("id"));
        return ResponseEntity.ok()
            .contentType(MediaType.parseMediaType("text/markdown;charset=UTF-8"))
            .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"conversation-" + thread.get("id") + ".md\"")
            .body(output -> {
                OutputStreamWriter writer = new OutputStreamWriter(output, StandardCharsets.UTF_8);
                writer.write("# " + (thread.get("title") == null ? "研究会话" : thread.get("title")) + "\n\n本文件包含该会话中的全部研究记录。\n");
                String previousTime = "", previousId = "";
                int index = 0;
                while (!upperId.isBlank()) {
                    List<Map<String, Object>> rows = jdbc.queryForList("""
                        SELECT r.id,r.topic,r.status,r.created_at,r.report FROM runs r
                        JOIN memberships m ON m.workspace_id=r.user_id AND m.subject=?
                        WHERE r.thread_id=? AND r.user_id=? AND (m.role='admin' OR r.created_by=m.subject)
                        AND (?='' OR (r.created_at,r.id)>(?,?)) AND (r.created_at,r.id)<=(?,?)
                        ORDER BY r.created_at,r.id LIMIT 1""",
                        identity.subject(), thread.get("id"), identity.workspaceId(), previousId, previousTime, previousId, upperTime, upperId);
                    if (rows.isEmpty()) break;
                    Map<String, Object> run = rows.getFirst();
                    writer.write("\n## " + (++index) + ". " + run.get("topic") + "\n\n- 状态：" + run.get("status") + "\n- 创建时间：" + run.get("created_at") + "\n\n");
                    String text = String.valueOf(run.get("report"));
                    if (text.isBlank()) writer.write("_此研究尚未生成 Markdown 报告。_");
                    for (int offset = 0; offset < text.length(); offset += 4096)
                        writer.write(text, offset, Math.min(4096, text.length() - offset));
                    writer.write('\n'); writer.flush();
                    previousTime = String.valueOf(run.get("created_at")); previousId = String.valueOf(run.get("id"));
                }
                writer.flush();
            });
    }

    private ResponseEntity<List<Map<String, Object>>> page(ReportPublicationService.Page page) {
        ResponseEntity.BodyBuilder response = ResponseEntity.ok();
        if (page.nextCursor() != null) response.header("X-Next-Cursor", page.nextCursor());
        return response.body(page.rows());
    }

    static ResponseEntity<byte[]> forwarded(AgentClient.AgentResponse response) {
        return ResponseEntity.status(response.status()).headers(response.headers()).body(response.body());
    }
}
