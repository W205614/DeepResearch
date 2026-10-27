package com.deepresearch.business.api;

import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.AgentClient;
import com.deepresearch.business.service.ReportPublicationService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AgentProxyController {
    private final WorkspaceAccess access;
    private final AgentClient agent;
    private final ReportPublicationService publications;

    public AgentProxyController(WorkspaceAccess access, AgentClient agent, ReportPublicationService publications) {
        this.access = access;
        this.agent = agent;
        this.publications = publications;
    }

    @RequestMapping({
        "/api/attachments", "/api/attachments/**", "/api/reports/*/source-rechecks", "/api/usage",
        "/api/documents", "/api/documents/**",
        "/api/config/check", "/api/config/probe", "/api/capabilities", "/api/status", "/api/metrics",
        "/api/data", "/api/data/**"
    })
    ResponseEntity<byte[]> proxy(@AuthenticationPrincipal Jwt jwt,
                                 @RequestHeader(value = "X-Workspace-ID", required = false) String requested,
                                 HttpServletRequest request) {
        WorkspaceIdentity identity = access.resolve(jwt, requested);
        if (request.getRequestURI().equals("/api/config/probe")) {
            String configured = System.getenv().getOrDefault("PLATFORM_OPERATOR_SUBJECTS", "");
            boolean operator = java.util.Arrays.stream(configured.split(","))
                .map(String::strip).anyMatch(identity.subject()::equals);
            if (!operator) throw new org.springframework.web.server.ResponseStatusException(
                org.springframework.http.HttpStatus.FORBIDDEN, "真实连接探测仅允许运维配置的主体执行");
        }
        authorizeMutation(identity, request);
        if(request.getRequestURI().endsWith("/source-rechecks"))
            publications.get(identity,request.getRequestURI().split("/")[3]);
        if ("DELETE".equals(request.getMethod()) && request.getRequestURI().equals("/api/data")
            && ("all".equals(request.getParameter("scope")) || "reports".equals(request.getParameter("scope"))))
            publications.requireNoPublicationsForWorkspace(identity);
        return ThreadController.forwarded(agent.forward(request));
    }

    private void authorizeMutation(WorkspaceIdentity identity, HttpServletRequest request) {
        String method = request.getMethod();
        String path = request.getRequestURI();
        if (path.startsWith("/api/data")) access.require(identity, "admin");
        else if ("DELETE".equals(method) && path.startsWith("/api/documents/")) access.require(identity, "admin");
        else if (!"GET".equals(method) && !path.equals("/api/documents/search")
            && !path.equals("/api/config/check") && !path.equals("/api/config/probe")) access.require(identity, "admin", "researcher");
    }
}
