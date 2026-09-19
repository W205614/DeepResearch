package com.deepresearch.business;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import com.deepresearch.business.api.Requests;
import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.AgentClient;
import com.deepresearch.business.service.OutboxService;
import com.deepresearch.business.service.ResearchCommandService;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpHeaders;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.jdbc.Sql;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.web.server.ResponseStatusException;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

@SpringBootTest(properties = {
    "deepresearch.outbox-dispatch-initial-delay-ms=3600000",
    "deepresearch.outbox-dispatch-delay-ms=3600000"
})
@AutoConfigureMockMvc
@Testcontainers
@Sql(scripts = "/business-test-schema.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD)
class BusinessBackendIntegrationTest {
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {};

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17.6-alpine");

    @DynamicPropertySource
    static void database(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("spring.security.oauth2.resourceserver.jwt.jwk-set-uri", () -> "http://127.0.0.1:1/test-jwks");
    }

    @Autowired MockMvc mvc;
    @Autowired JdbcTemplate jdbc;
    @Autowired ObjectMapper mapper;
    @Autowired ResearchCommandService commands;
    @Autowired OutboxService outbox;
    @Autowired DeepResearchProperties properties;
    @Autowired MeterRegistry registry;
    @MockitoBean AgentClient agent;

    @BeforeEach
    void defaults() {
        properties.setOutboxBatchSize(20);
        properties.setOutboxLeaseSeconds(30);
        properties.setOutboxMaxAttempts(8);
        properties.setOutboxBaseDelaySeconds(1);
        properties.setOutboxMaxDelaySeconds(2);
        properties.setMaxQueuedRuns(20);
        when(agent.internal(anyString(), anyString())).thenReturn(accepted());
    }

    @Test
    void publicApiRequiresJwt() throws Exception {
        mvc.perform(get("/api/workspaces")).andExpect(status().isUnauthorized());
    }

    @Test
    void viewerCannotCreateResearch() throws Exception {
        workspace("viewer-space", "viewer", "viewer", 2);

        mvc.perform(post("/api/research/runs")
                .with(jwt().jwt(token -> token.subject("viewer")))
                .header("X-Workspace-ID", "viewer-space")
                .contentType("application/json")
                .content(runBody("viewer-request", "viewer cannot create")))
            .andExpect(status().isForbidden());

        assertThat(count("runs")).isZero();
    }

    @Test
    void documentReplacementIsProxiedForResearcherButDeniedForViewer() throws Exception {
        workspace("document-space", "researcher", "researcher", 2);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('document-space','viewer','viewer','now')");
        when(agent.forward(any(HttpServletRequest.class))).thenReturn(
            new AgentClient.AgentResponse(202, new HttpHeaders(), "{}".getBytes()));

        mvc.perform(put("/api/documents/document-1")
                .with(jwt().jwt(token -> token.subject("viewer")))
                .header("X-Workspace-ID", "document-space")
                .contentType("multipart/form-data; boundary=test")
                .content("--test--"))
            .andExpect(status().isForbidden());
        mvc.perform(put("/api/documents/document-1")
                .with(jwt().jwt(token -> token.subject("researcher")))
                .header("X-Workspace-ID", "document-space")
                .contentType("multipart/form-data; boundary=test")
                .content("--test--"))
            .andExpect(status().isAccepted());

        verify(agent, times(1)).forward(any(HttpServletRequest.class));
    }

    @Test
    void repeatedRequestReturnsSameRunAndPersistsOneOutboxCommand() throws Exception {
        MvcResult first = mvc.perform(post("/api/research/runs")
                .with(jwt().jwt(token -> token.subject("alice").claim("preferred_username", "Alice")))
                .contentType("application/json")
                .content(runBody("stable-request-01", "研究 Java 与 Agent 边界")))
            .andExpect(status().isAccepted())
            .andReturn();
        MvcResult second = mvc.perform(post("/api/research/runs")
                .with(jwt().jwt(token -> token.subject("alice").claim("preferred_username", "Alice")))
                .contentType("application/json")
                .content(runBody("stable-request-01", "研究 Java 与 Agent 边界")))
            .andExpect(status().isAccepted())
            .andReturn();

        assertThat(body(first).get("id")).isEqualTo(body(second).get("id"));
        assertThat(count("runs")).isEqualTo(1);
        assertThat(count("business_outbox")).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_logs WHERE action='research.create'", Integer.class))
            .isEqualTo(1);
    }

    @Test
    void concurrentReplayIsSerializedByWorkspaceAdmissionLock() throws Exception {
        workspace("concurrent", "owner", "admin", 2);
        WorkspaceIdentity identity = new WorkspaceIdentity("concurrent", "owner", "admin", "Owner");
        Requests.Run request = request("same-concurrent-request", "并发幂等测试");

        List<Map<String, Object>> results = concurrently(
            () -> commands.create(identity, request),
            () -> commands.create(identity, request));

        assertThat(results).hasSize(2);
        assertThat(results.stream().map(row -> row.get("id")).distinct()).hasSize(1);
        assertThat(count("runs")).isEqualTo(1);
        assertThat(count("business_outbox")).isEqualTo(1);
    }

    @Test
    void concurrentAdmissionCannotExceedWorkspaceLimit() throws Exception {
        workspace("limited", "owner", "admin", 1);
        WorkspaceIdentity identity = new WorkspaceIdentity("limited", "owner", "admin", "Owner");

        List<Object> results = concurrentResults(
            () -> commands.create(identity, request("limit-request-01", "第一个研究")),
            () -> commands.create(identity, request("limit-request-02", "第二个研究")));

        assertThat(results.stream().filter(Map.class::isInstance).count()).isEqualTo(1);
        assertThat(results.stream().filter(ResponseStatusException.class::isInstance).count()).isEqualTo(1);
        assertThat(count("runs")).isEqualTo(1);
    }

    @Test
    void concurrentWorkspacesCannotExceedGlobalQueueLimit() throws Exception {
        properties.setMaxQueuedRuns(1);
        workspace("global-a", "owner-a", "admin", 2);
        workspace("global-b", "owner-b", "admin", 2);
        WorkspaceIdentity first = new WorkspaceIdentity("global-a", "owner-a", "admin", "Owner A");
        WorkspaceIdentity second = new WorkspaceIdentity("global-b", "owner-b", "admin", "Owner B");

        List<Object> results = concurrentResults(
            () -> commands.create(first, request("global-request-01", "第一个全局任务")),
            () -> commands.create(second, request("global-request-02", "第二个全局任务")));

        assertThat(results.stream().filter(Map.class::isInstance).count()).isEqualTo(1);
        assertThat(results.stream().filter(ResponseStatusException.class::isInstance).count()).isEqualTo(1);
        assertThat(count("runs")).isEqualTo(1);
    }

    @Test
    void outboxFailureRollsBackBusinessTransaction() {
        workspace("rollback", "owner", "admin", 2);
        jdbc.execute("DROP TABLE business_outbox");
        WorkspaceIdentity identity = new WorkspaceIdentity("rollback", "owner", "admin", "Owner");

        assertThatThrownBy(() -> commands.create(identity, request("rollback-request", "事务回滚")))
            .isInstanceOf(DataAccessException.class);

        assertThat(count("runs")).isZero();
        assertThat(count("threads")).isZero();
        assertThat(count("events")).isZero();
        assertThat(count("audit_logs")).isZero();
    }

    @Test
    void cancelAndResumeUseExplicitStateTransitions() throws Exception {
        MvcResult created = mvc.perform(post("/api/research/runs")
                .with(jwt().jwt(token -> token.subject("alice")))
                .contentType("application/json")
                .content(runBody("lifecycle-request", "生命周期测试")))
            .andExpect(status().isAccepted()).andReturn();
        String runId = String.valueOf(body(created).get("id"));

        mvc.perform(post("/api/research/runs/{runId}/cancel", runId)
                .with(jwt().jwt(token -> token.subject("alice"))))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.status").value("cancelled"));
        mvc.perform(post("/api/research/runs/{runId}/resume", runId)
                .with(jwt().jwt(token -> token.subject("alice"))))
            .andExpect(status().isAccepted())
            .andExpect(jsonPath("$.status").value("queued"));

        assertThat(jdbc.queryForList(
            "SELECT event_type FROM business_outbox WHERE aggregate_id=? ORDER BY created_at,event_type", String.class, runId))
            .containsExactlyInAnyOrder("research.schedule", "research.abort", "research.resume");
    }

    @Test
    void resumeHonorsGlobalQueueCapacityWithoutDoubleCountingInterruptedRun() {
        properties.setMaxQueuedRuns(2);
        workspace("resume-space", "owner", "admin", 3);
        run("other-run", "resume-space");
        run("interrupted-run", "resume-space");
        run("failed-run", "resume-space");
        jdbc.update("UPDATE runs SET status='interrupted' WHERE id='interrupted-run'");
        jdbc.update("UPDATE runs SET status='failed' WHERE id='failed-run'");
        WorkspaceIdentity identity = new WorkspaceIdentity("resume-space", "owner", "admin", "Owner");

        assertThat(commands.resume(identity, "interrupted-run").get("status")).isEqualTo("queued");
        assertThatThrownBy(() -> commands.resume(identity, "failed-run"))
            .isInstanceOf(ResponseStatusException.class)
            .satisfies(error -> assertThat(((ResponseStatusException) error).getStatusCode().value()).isEqualTo(503));
    }

    @Test
    void activeOutboxCommandIsDeduplicated() {
        run("outbox-run", "workspace");

        outbox.enqueue("outbox-run", "research.schedule", "{\"resume\":false}");
        outbox.enqueue("outbox-run", "research.schedule", "{\"resume\":false}");

        assertThat(count("business_outbox")).isEqualTo(1);
    }

    @Test
    void twoDispatchersDeliverClaimedMessageOnlyOnce() throws Exception {
        run("claimed-run", "workspace");
        outbox.enqueue("claimed-run", "research.schedule", "{\"resume\":false}");
        OutboxService second = new OutboxService(jdbc, agent, properties, new SimpleMeterRegistry());
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        when(agent.internal(anyString(), anyString())).thenAnswer(invocation -> {
            entered.countDown();
            assertThat(release.await(10, TimeUnit.SECONDS)).isTrue();
            return accepted();
        });

        try (ExecutorService pool = Executors.newVirtualThreadPerTaskExecutor()) {
            Future<?> first = pool.submit(outbox::dispatch);
            assertThat(entered.await(10, TimeUnit.SECONDS)).isTrue();
            Future<?> competing = pool.submit(second::dispatch);
            competing.get(10, TimeUnit.SECONDS);
            release.countDown();
            first.get(10, TimeUnit.SECONDS);
        }

        verify(agent, times(1)).internal(anyString(), anyString());
        assertThat(jdbc.queryForObject("SELECT status FROM business_outbox", String.class)).isEqualTo("delivered");
        assertThat(jdbc.queryForObject("SELECT attempts FROM business_outbox", Integer.class)).isEqualTo(1);
    }

    @Test
    void expiredLeaseIsRecoveredAndDelivered() {
        run("lease-run", "workspace");
        jdbc.update("""
            INSERT INTO business_outbox(id,aggregate_id,event_type,payload,status,attempts,next_attempt_at,
                created_at,updated_at,lease_owner,lease_until)
            VALUES('lease-message','lease-run','research.schedule','{}','processing',1,0,?,?,?,?)""",
            "now", "now", "crashed-instance", Instant.now().getEpochSecond() - 1);

        outbox.dispatch();

        Map<String, Object> row = jdbc.queryForMap(
            "SELECT status,attempts,lease_owner FROM business_outbox WHERE id='lease-message'");
        assertThat(row.get("status")).isEqualTo("delivered");
        assertThat(row.get("attempts")).isEqualTo(2);
        assertThat(row.get("lease_owner")).isEqualTo("");
    }

    @Test
    void retryExhaustionCreatesInspectableAndRecoverableDeadLetter() {
        properties.setOutboxMaxAttempts(2);
        run("dead-run", "workspace");
        outbox.enqueue("dead-run", "research.schedule", "{\"resume\":false}");
        when(agent.internal(anyString(), anyString()))
            .thenReturn(new AgentClient.AgentResponse(503, new HttpHeaders(), new byte[0]));

        outbox.dispatch();
        jdbc.update("UPDATE business_outbox SET next_attempt_at=0");
        outbox.dispatch();

        Map<String, Object> row = jdbc.queryForMap("SELECT * FROM business_outbox");
        assertThat(row.get("status")).isEqualTo("dead");
        assertThat(row.get("attempts")).isEqualTo(2);
        assertThat(row.get("last_error")).isEqualTo("agent_http_503");
        assertThat(registry.get("deepresearch.business.outbox.depth").tag("status", "dead").gauge().value())
            .isEqualTo(1);
        assertThat(outbox.deadLetters("workspace")).hasSize(1);
        assertThat(outbox.retryDeadLetter(String.valueOf(row.get("id")), "workspace")).isTrue();
        assertThat(jdbc.queryForObject("SELECT status FROM business_outbox", String.class)).isEqualTo("pending");
        assertThat(jdbc.queryForObject("SELECT attempts FROM business_outbox", Integer.class)).isZero();
    }

    @Test
    void outboxDeadLettersAreAdminOnly() throws Exception {
        workspace("workspace", "admin", "admin", 2);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('workspace','viewer','viewer','now')");
        run("dead-api-run", "workspace");
        jdbc.update("""
            INSERT INTO business_outbox(id,aggregate_id,event_type,payload,status,attempts,next_attempt_at,
                created_at,updated_at,last_error)
            VALUES('dead-api-message','dead-api-run','research.schedule','{}','dead',8,0,'now','now','agent_http_503')""");

        mvc.perform(get("/api/workspaces/workspace/outbox/dead-letters")
                .with(jwt().jwt(token -> token.subject("viewer")))
                .header("X-Workspace-ID", "workspace"))
            .andExpect(status().isForbidden());
        mvc.perform(get("/api/workspaces/workspace/outbox/dead-letters")
                .with(jwt().jwt(token -> token.subject("admin")))
                .header("X-Workspace-ID", "workspace"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].id").value("dead-api-message"));
        mvc.perform(post("/api/workspaces/workspace/outbox/dead-letters/dead-api-message/retry")
                .with(jwt().jwt(token -> token.subject("admin")))
                .header("X-Workspace-ID", "workspace"))
            .andExpect(status().isAccepted())
            .andExpect(jsonPath("$.ok").value(true));
        assertThat(jdbc.queryForObject("SELECT status FROM business_outbox WHERE id='dead-api-message'", String.class))
            .isEqualTo("pending");
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_logs WHERE action='outbox.retry'", Integer.class))
            .isEqualTo(1);
    }

    private void workspace(String id, String subject, String role, int limit) {
        jdbc.update("INSERT INTO workspaces(id,name,created_by,created_at) VALUES(?,?,?,?)", id, id, subject, "now");
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?)", id, subject, role, "now");
        jdbc.update("INSERT INTO workspace_limits(workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit) VALUES(?,0,0,?)",
            id, limit);
    }

    private void run(String id, String workspaceId) {
        jdbc.update("INSERT INTO threads(id,user_id,title,created_at,thread_key) VALUES(?,?,?,?,?)",
            id + "-thread", workspaceId, "Test", "now", id + "-thread");
        jdbc.update("""
            INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id,
                created_by,data_policy,deadline_at)
            VALUES(?,?,?,?,?,'queued','now','now',?,?,?,?)""",
            id, workspaceId, id + "-thread", "Test", "auto", id + "-request", workspaceId, "public",
            Instant.now().getEpochSecond() + 1200);
    }

    private Requests.Run request(String requestId, String topic) {
        Requests.Run request = new Requests.Run();
        request.setClientRequestId(requestId);
        request.setTopic(topic);
        request.setDataPolicy("public");
        return request;
    }

    private String runBody(String requestId, String topic) throws Exception {
        return mapper.writeValueAsString(Map.of(
            "client_request_id", requestId,
            "topic", topic,
            "data_policy", "public"));
    }

    private Map<String, Object> body(MvcResult result) throws Exception {
        return mapper.readValue(result.getResponse().getContentAsByteArray(), MAP);
    }

    private int count(String table) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class);
    }

    private <T> List<T> concurrently(Callable<T> first, Callable<T> second) throws Exception {
        List<Object> results = concurrentResults(first, second);
        List<T> values = new ArrayList<>();
        for (Object result : results) {
            if (result instanceof Throwable error) throw new ExecutionException(error);
            @SuppressWarnings("unchecked") T value = (T) result;
            values.add(value);
        }
        return values;
    }

    private List<Object> concurrentResults(Callable<?> first, Callable<?> second) throws Exception {
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        Callable<Object> guardedFirst = guarded(first, ready, start);
        Callable<Object> guardedSecond = guarded(second, ready, start);
        try (ExecutorService pool = Executors.newVirtualThreadPerTaskExecutor()) {
            Future<Object> left = pool.submit(guardedFirst);
            Future<Object> right = pool.submit(guardedSecond);
            assertThat(ready.await(10, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            return List.of(left.get(10, TimeUnit.SECONDS), right.get(10, TimeUnit.SECONDS));
        }
    }

    private Callable<Object> guarded(Callable<?> action, CountDownLatch ready, CountDownLatch start) {
        return () -> {
            ready.countDown();
            start.await(10, TimeUnit.SECONDS);
            try {
                return action.call();
            } catch (Throwable error) {
                return error;
            }
        };
    }

    private AgentClient.AgentResponse accepted() {
        return new AgentClient.AgentResponse(202, new HttpHeaders(), new byte[0]);
    }
}
