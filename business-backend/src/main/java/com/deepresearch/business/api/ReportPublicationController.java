package com.deepresearch.business.api;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.ReportPublicationService;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ReportPublicationController {
    private final WorkspaceAccess access;
    private final ReportPublicationService publications;

    public ReportPublicationController(WorkspaceAccess access, ReportPublicationService publications) {
        this.access = access;
        this.publications = publications;
    }

    @PostMapping("/api/research/runs/{runId}/publication")
    ResponseEntity<Map<String, Object>> submit(@PathVariable String runId, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return ResponseEntity.status(201).body(publications.submit(identity(jwt, requested), runId));
    }

    @GetMapping("/api/reports")
    List<Map<String, Object>> published(@AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return publications.published(identity(jwt, requested));
    }

    @GetMapping("/api/reports/pending")
    List<Map<String, Object>> pending(@AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return publications.pending(identity(jwt, requested));
    }

    @GetMapping("/api/reports/archived")
    List<Map<String, Object>> archived(@AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return publications.archived(identity(jwt, requested));
    }

    @GetMapping("/api/reports/{reportId}")
    Map<String, Object> detail(@PathVariable String reportId, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return publications.get(identity(jwt, requested), reportId);
    }

    @GetMapping("/api/reports/{reportId}/download")
    ResponseEntity<byte[]> download(@PathVariable String reportId, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        WorkspaceIdentity identity = identity(jwt, requested);
        Map<String, Object> report = publications.get(identity, reportId);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("text/markdown;charset=UTF-8"))
            .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"report-" + reportId + ".md\"")
            .body(String.valueOf(report.get("report_markdown")).getBytes(StandardCharsets.UTF_8));
    }

    @PostMapping("/api/reports/{reportId}/approve")
    Map<String, Object> approve(@PathVariable String reportId, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        return publications.approve(identity(jwt, requested), reportId);
    }

    @PostMapping("/api/reports/{reportId}/reject")
    Map<String, Object> reject(@PathVariable String reportId, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
            @Valid @RequestBody Requests.ReviewReason body) {
        return publications.reject(identity(jwt, requested), reportId, body.reason());
    }

    @PostMapping("/api/reports/{reportId}/withdraw")
    Map<String, Object> withdraw(@PathVariable String reportId, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
            @Valid @RequestBody Requests.ReviewReason body) {
        return publications.withdraw(identity(jwt, requested), reportId, body.reason());
    }

    @DeleteMapping("/api/reports/{reportId}")
    ResponseEntity<Void> delete(@PathVariable String reportId, @AuthenticationPrincipal Jwt jwt,
            @RequestHeader(value = "X-Workspace-ID", required = false) String requested) {
        publications.delete(identity(jwt, requested), reportId);
        return ResponseEntity.noContent().build();
    }

    private WorkspaceIdentity identity(Jwt jwt, String requested) {
        return access.resolve(jwt, requested);
    }
}
