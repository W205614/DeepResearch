package com.deepresearch.business.api;

import java.util.Map;

import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.service.AgentClient;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class HealthController {
    private final JdbcTemplate jdbc;
    private final AgentClient agent;
    private final DeepResearchProperties properties;

    public HealthController(JdbcTemplate jdbc, AgentClient agent, DeepResearchProperties properties) {
        this.jdbc = jdbc;
        this.agent = agent;
        this.properties = properties;
    }

    @GetMapping({"/healthz", "/livez"})
    Map<String, String> live() { return Map.of("status", "ok"); }

    @GetMapping("/readyz")
    Map<String, String> ready() {
        try {
            jdbc.queryForObject("SELECT 1", Integer.class);
            AgentClient.AgentResponse response = agent.get("/readyz");
            if (!response.successful()) throw new IllegalStateException("agent not ready");
            if (properties.resolvedInternalToken().isBlank()) throw new IllegalStateException("internal token unavailable");
            return Map.of("status", "ready");
        } catch (RuntimeException error) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "业务数据库或 Agent 服务未就绪", error);
        }
    }

    @GetMapping("/api/auth/config")
    Map<String, String> authConfig() {
        return Map.of("mode", properties.getAuthMode(), "issuer", properties.getOidcIssuer());
    }
}
