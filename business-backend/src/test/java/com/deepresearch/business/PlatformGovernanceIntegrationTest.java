package com.deepresearch.business;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.security.SharedApiRateLimiter;
import com.deepresearch.business.service.AgentClient;
import com.deepresearch.business.service.ResearchCommandService;
import com.deepresearch.business.service.ReportPublicationService;
import com.deepresearch.business.security.WorkspaceIdentity;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.jdbc.Sql;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

@SpringBootTest(properties = {"deepresearch.platform-operator-subjects=operator,second-operator",
    "deepresearch.outbox-dispatch-initial-delay-ms=3600000", "deepresearch.auth-mode=oidc"})
@AutoConfigureMockMvc
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@Sql(scripts = "/business-test-schema.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD)
class PlatformGovernanceIntegrationTest {
    @Container static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer("postgres:17.6-alpine");
    @Container static final GenericContainer<?> REDIS = new GenericContainer<>(
        "redis:7.4.11-alpine@sha256:57e4255cc971b1c8d6f8a6ca283ac01bc3ecdd914273ef6bfaede87aaecd6541")
        .withExposedPorts(6379);

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("spring.security.oauth2.resourceserver.jwt.jwk-set-uri", () -> "http://127.0.0.1:1/jwks");
        registry.add("deepresearch.redis-url", () -> "redis://" + REDIS.getHost() + ":" + REDIS.getMappedPort(6379) + "/0");
    }

    @Autowired MockMvc mvc;
    @Autowired JdbcTemplate jdbc;
    @Autowired DeepResearchProperties properties;
    @Autowired StringRedisTemplate redis;
    @Autowired SharedApiRateLimiter limiter;
    @Autowired ResearchCommandService commands;
    @Autowired ReportPublicationService publications;
    @MockitoBean AgentClient agent;

    @BeforeEach
    void seed() {
        properties.setDeploymentId("governance-test-" + UUID.randomUUID());
        properties.setApiRateLimitPerSecond(20);
        properties.setApiRateLimitBurst(40);
        properties.setApiGlobalRateLimitPerSecond(250);
        properties.setApiGlobalRateLimitBurst(250);
        for (String subject : List.of("operator", "second-operator", "owner", "successor", "reader"))
            jdbc.update("INSERT INTO platform_users(subject,display_name,created_at,updated_at) VALUES(?,?,?,?)", subject, subject, "2026-11-07T00:00:00Z", "2026-11-07T00:00:00Z");
        jdbc.update("INSERT INTO workspaces(id,name,created_by,owner_subject,created_at) VALUES('space','Research','owner','owner','2026-11-07T00:00:00Z')");
        jdbc.update("INSERT INTO memberships VALUES('space','owner','admin','2026-11-07T00:00:00Z'),('space','reader','viewer','2026-11-07T00:00:00Z')");
        jdbc.update("INSERT INTO workspace_limits VALUES('space',0,0,2)");
        jdbc.update("INSERT INTO threads(id,user_id,title,created_at,created_by) VALUES('thread','space','private-topic','2026-11-07T00:00:00Z','owner')");
        jdbc.update("""
            INSERT INTO runs(id,user_id,thread_id,topic,status,created_by,created_at,updated_at,report)
            VALUES('run','space','thread','private-topic','running','owner','2026-11-07T00:00:00Z','2026-11-07T00:00:00Z','private-report')""");
    }

    private RequestPostProcessor account(String subject) {
        return jwt().jwt(token -> token.subject(subject).issuedAt(Instant.now().minusSeconds(60)));
    }

    @Test @Order(1)
    void metadataRequiresOperatorAndNeverRevealsResearchContent() throws Exception {
        mvc.perform(get("/api/admin/overview").with(account("reader"))).andExpect(status().isForbidden());
        String body = mvc.perform(get("/api/admin/overview").with(account("operator")))
            .andExpect(status().isOk()).andExpect(jsonPath("$.limits.active_runs").value(5))
            .andExpect(jsonPath("$.workspaces[0].owner_subject").value("owner"))
            .andExpect(jsonPath("$.usage.recorded").value(false))
            .andExpect(jsonPath("$.usage.period_start").value(org.hamcrest.Matchers.nullValue()))
            .andExpect(jsonPath("$.usage.calls").value(0))
            .andExpect(jsonPath("$.usage.reserved_tokens").value(0))
            .andExpect(jsonPath("$.usage.pending_tokens").value(0))
            .andExpect(jsonPath("$.usage.unknown_tokens").value(0))
            .andExpect(jsonPath("$.usage.actual_input").value(0))
            .andExpect(jsonPath("$.usage.actual_output").value(0))
            .andExpect(jsonPath("$.dependencies.database").value("reachable"))
            .andExpect(jsonPath("$.dependencies.redis").value("reachable"))
            .andExpect(jsonPath("$.dependencies.agent").value("not_checked"))
            .andExpect(jsonPath("$.read_only_reasons").isEmpty())
            .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("private-topic", "private-report", "redis://", "password");
        mvc.perform(get("/api/auth/session").with(account("reader")))
            .andExpect(status().isOk()).andExpect(jsonPath("$.platform_operator").value(false));
        mvc.perform(get("/api/admin/users").with(account("operator")).param("limit", "2"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(2))
            .andExpect(jsonPath("$.next_cursor").isString());
    }

    @Test @Order(2)
    void productionJwtWithoutIssuedAtCannotBootstrapAnAccount() throws Exception {
        mvc.perform(get("/api/workspaces").with(jwt().jwt(token -> token.subject("unknown").claims(claims -> claims.remove("iat")))))
            .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("session_revoked"));
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM platform_users WHERE subject='unknown'", Integer.class)).isZero();
    }

    @Test @Order(3)
    void transferPreservesCreatorAndDisableCancelsWorkWhileOldTokensStayRevoked() throws Exception {
        mvc.perform(put("/api/admin/users/owner/status").with(account("operator")).contentType(MediaType.APPLICATION_JSON)
            .content("{\"status\":\"disabled\",\"reason\":\"offboarding\"}")).andExpect(status().isConflict());
        mvc.perform(post("/api/admin/workspaces/space/transfer").with(account("operator")).contentType(MediaType.APPLICATION_JSON)
            .content("{\"target_subject\":\"successor\",\"reason\":\"handover\"}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.previous_owner").value("owner"));
        assertThat(jdbc.queryForObject("SELECT created_by FROM workspaces WHERE id='space'", String.class)).isEqualTo("owner");
        assertThat(jdbc.queryForObject("SELECT role FROM memberships WHERE workspace_id='space' AND subject='owner'", String.class)).isEqualTo("researcher");
        mvc.perform(put("/api/admin/users/owner/status").with(account("operator")).contentType(MediaType.APPLICATION_JSON)
            .content("{\"status\":\"disabled\",\"reason\":\"offboarding\"}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("disabled"));
        assertThat(jdbc.queryForObject("SELECT status FROM runs WHERE id='run'", String.class)).isEqualTo("cancelled");
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM business_outbox WHERE aggregate_id='run' AND event_type='research.abort'", Integer.class)).isEqualTo(1);
        mvc.perform(get("/api/research/runs/run").with(account("owner")).header("X-Workspace-ID", "space"))
            .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("account_disabled"));
        mvc.perform(put("/api/admin/users/owner/status").with(account("operator")).contentType(MediaType.APPLICATION_JSON)
            .content("{\"status\":\"active\",\"reason\":\"reinstatement\"}")).andExpect(status().isOk());
        mvc.perform(get("/api/auth/session").with(account("owner"))).andExpect(status().isUnauthorized());
        long cutoff = jdbc.queryForObject("SELECT revoked_before FROM platform_users WHERE subject='owner'", Long.class);
        mvc.perform(get("/api/auth/session").with(jwt().jwt(token -> token.subject("owner").issuedAt(Instant.ofEpochSecond(cutoff + 1)))))
            .andExpect(status().isOk());
        mvc.perform(get("/api/admin/audit").with(account("operator")).param("action", "account.disabled"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(1));
    }

    @Test @Order(4)
    void maintenanceBarrierBlocksMutationsButLeavesMetadataReadable() throws Exception {
        jdbc.update("INSERT INTO metadata VALUES('embedding_migration_barrier','fixture')");
        mvc.perform(put("/api/admin/users/reader/status").with(account("operator")).contentType(MediaType.APPLICATION_JSON)
            .content("{\"status\":\"disabled\",\"reason\":\"maintenance\"}"))
            .andExpect(status().isServiceUnavailable()).andExpect(jsonPath("$.code").value("embedding_migration_active"));
        mvc.perform(get("/api/admin/overview").with(account("operator")))
            .andExpect(status().isOk()).andExpect(jsonPath("$.read_only").value(true))
            .andExpect(jsonPath("$.read_only_reasons[0]").value("embedding_migration_active"));
        assertThat(jdbc.queryForObject("SELECT status FROM platform_users WHERE subject='reader'", String.class)).isEqualTo("active");
    }

    @Test @Order(5)
    void independentLimitersShareOneAtomicBudget() throws Exception {
        properties.setApiRateLimitPerSecond(1);
        properties.setApiRateLimitBurst(5);
        properties.setApiGlobalRateLimitPerSecond(1);
        properties.setApiGlobalRateLimitBurst(5);
        SharedApiRateLimiter second = new SharedApiRateLimiter(redis, properties, new SimpleMeterRegistry());
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            List<Future<SharedApiRateLimiter.Result>> calls = java.util.stream.IntStream.range(0, 32)
                .mapToObj(index -> executor.submit(() -> (index % 2 == 0 ? limiter : second).consume("same-subject"))).toList();
            long allowed = 0;
            for (var result : calls) if (result.get() == SharedApiRateLimiter.Result.ALLOWED) allowed++;
            assertThat(allowed).isEqualTo(5);
        }
    }

    @Test @Order(6)
    void concurrentOperatorsCannotDisableEachOtherAndLoseFinalAdministrator() throws Exception {
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            var first = executor.submit(() -> mvc.perform(put("/api/admin/users/second-operator/status")
                .with(account("operator")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"disabled\",\"reason\":\"concurrent offboarding\"}"))
                .andReturn().getResponse().getStatus());
            var second = executor.submit(() -> mvc.perform(put("/api/admin/users/operator/status")
                .with(account("second-operator")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"disabled\",\"reason\":\"concurrent offboarding\"}"))
                .andReturn().getResponse().getStatus());
            assertThat(List.of(first.get(), second.get())).containsExactlyInAnyOrder(200, 403);
        }
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM platform_users WHERE subject IN ('operator','second-operator') AND status='active'", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM platform_audit_logs WHERE action='account.disabled'", Integer.class)).isEqualTo(1);
    }

    @Test @Order(7)
    void businessTransactionsRecheckDisabledAccountBeforeCreatingOrPublishing() {
        WorkspaceIdentity identity = new WorkspaceIdentity("space", "owner", "admin", "Owner", Instant.now().minusSeconds(60));
        jdbc.update("UPDATE platform_users SET status='disabled' WHERE subject='owner'");
        assertThatThrownBy(() -> commands.createThread(identity, "must not be created"))
            .isInstanceOf(org.springframework.web.server.ResponseStatusException.class)
            .satisfies(error -> assertThat(((org.springframework.web.server.ResponseStatusException) error).getStatusCode().value()).isEqualTo(403));
        assertThatThrownBy(() -> publications.submit(identity, "run"))
            .isInstanceOf(org.springframework.web.server.ResponseStatusException.class)
            .satisfies(error -> assertThat(((org.springframework.web.server.ResponseStatusException) error).getStatusCode().value()).isEqualTo(403));
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM threads", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM report_publications", Integer.class)).isZero();
        jdbc.update("UPDATE platform_users SET status='active',revoked_before=? WHERE subject='owner'", Instant.now().getEpochSecond());
        assertThatThrownBy(() -> commands.createThread(identity, "old authorization after reinstatement"))
            .isInstanceOf(org.springframework.web.server.ResponseStatusException.class)
            .satisfies(error -> assertThat(((org.springframework.web.server.ResponseStatusException) error).getStatusCode().value()).isEqualTo(401));
        assertThatThrownBy(() -> publications.submit(identity, "run"))
            .isInstanceOf(org.springframework.web.server.ResponseStatusException.class)
            .satisfies(error -> assertThat(((org.springframework.web.server.ResponseStatusException) error).getStatusCode().value()).isEqualTo(401));
    }

    @Test @Order(8)
    void overviewUsesOnlyTheLatestRecordedPlatformPeriodWithoutDoubleCountingDimensions() throws Exception {
        jdbc.update("""
            INSERT INTO usage_periods(scope_key,period_start,calls,reserved_tokens,pending_tokens,unknown_tokens,actual_input,actual_output)
            VALUES('platform',100,9000,9000,9000,9000,9000,9000),
              ('platform',200,3,700,50,60,110,120),
              ('subject:private-subject',300,9000,9000,9000,9000,9000,9000),
              ('workspace:private-workspace',300,9000,9000,9000,9000,9000,9000),
              ('account:private-provider-hash',300,9000,9000,9000,9000,9000,9000)""");
        mvc.perform(get("/api/admin/overview").with(account("reader"))).andExpect(status().isForbidden());
        String body = mvc.perform(get("/api/admin/overview").with(account("operator")))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.usage.recorded").value(true)).andExpect(jsonPath("$.usage.period_start").value(200))
            .andExpect(jsonPath("$.usage.calls").value(3)).andExpect(jsonPath("$.usage.reserved_tokens").value(700))
            .andExpect(jsonPath("$.usage.pending_tokens").value(50)).andExpect(jsonPath("$.usage.unknown_tokens").value(60))
            .andExpect(jsonPath("$.usage.actual_input").value(110)).andExpect(jsonPath("$.usage.actual_output").value(120))
            .andExpect(jsonPath("$.dependencies.database").value("reachable"))
            .andExpect(jsonPath("$.dependencies.redis").value("reachable"))
            .andExpect(jsonPath("$.dependencies.agent").value("not_checked"))
            .andExpect(jsonPath("$.dependencies.vector_store").value("not_checked"))
            .andExpect(jsonPath("$.dependencies.object_store").value("not_checked"))
            .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("private-provider-hash", "private-subject", "private-workspace", "scope_key",
            "private-topic", "private-report", "redis://", "password");
    }

    @Test @Order(99)
    void actualRedisOutageKeepsHistoricalReadsAndRejectsWrites() throws Exception {
        REDIS.stop();
        mvc.perform(get("/api/workspaces").with(account("owner")))
            .andExpect(status().isOk()).andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.header().string("X-DeepResearch-Read-Only", "true"));
        mvc.perform(get("/api/admin/overview").with(account("operator")))
            .andExpect(status().isOk()).andExpect(jsonPath("$.read_only").value(true))
            .andExpect(jsonPath("$.dependencies.database").value("reachable"))
            .andExpect(jsonPath("$.dependencies.redis").value("unavailable"))
            .andExpect(jsonPath("$.read_only_reasons[0]").value("redis_unavailable"));
        mvc.perform(put("/api/admin/users/reader/status").with(account("operator")).contentType(MediaType.APPLICATION_JSON)
            .content("{\"status\":\"disabled\",\"reason\":\"outage\"}"))
            .andExpect(status().isServiceUnavailable()).andExpect(jsonPath("$.code").value("read_only"));
        assertThat(jdbc.queryForObject("SELECT status FROM platform_users WHERE subject='reader'", String.class)).isEqualTo("active");
    }
}
