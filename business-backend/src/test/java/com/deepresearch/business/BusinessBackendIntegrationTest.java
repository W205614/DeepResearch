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
import com.deepresearch.business.service.ReportPublicationService;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
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
import org.testcontainers.postgresql.PostgreSQLContainer;
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
    static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer("postgres:17.6-alpine");

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
    @Autowired ReportPublicationService publications;
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

    @Test
    void reportReviewFreezesEvidenceAndRestrictsOldDraftRoutes() throws Exception {
        workspace("review-space", "writer", "researcher", 2);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('review-space','reviewer','admin','now'),('review-space','reader','viewer','now')");
        run("review-run", "review-space");
        jdbc.update("UPDATE threads SET created_by='writer' WHERE id='review-run-thread'");
        jdbc.update("""
            UPDATE runs SET created_by='writer',status='completed',report='# Original [L-1]',
            sources='[{"id":"L-1","kind":"local","title":"资料","text":"证据","locator":"第 2 页","chunk_id":"chunk-1","document_id":"doc-1","document_hash":"hash-1","index_version":1}]',
            validation='{"verification_contract":"verification-v3-claim-span","quality":"complete","checked_claims":1,"supported_claims":1}'
            WHERE id='review-run'""");
        jdbc.update("INSERT INTO documents(id,user_id,status,hash,index_version) VALUES('doc-1','review-space','ready','hash-1',1)");
        jdbc.update("INSERT INTO document_policies(document_id,workspace_id,owner_subject) VALUES('doc-1','review-space','writer')");
        jdbc.update("INSERT INTO chunks(id,document_id,user_id,text) VALUES('chunk-1','doc-1','review-space','证据')");

        mvc.perform(get("/api/research/runs/review-run").with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNotFound());
        mvc.perform(get("/api/research/runs/review-run/events").with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNotFound());
        mvc.perform(get("/api/threads/review-run-thread/report").with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNotFound());
        mvc.perform(get("/api/threads").with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isOk()).andExpect(jsonPath("$").isEmpty());

        MvcResult submitted = mvc.perform(post("/api/research/runs/review-run/publication")
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isCreated()).andExpect(jsonPath("$.sources[0].original_available").value(true))
            .andReturn();
        String id = String.valueOf(body(submitted).get("id"));
        mvc.perform(post("/api/research/runs/review-run/publication")
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isCreated()).andExpect(jsonPath("$.id").value(id));
        assertThat(count("report_publications")).isEqualTo(1);
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNotFound());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/threads/review-run-thread")
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isConflict());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/data?scope=reports")
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isConflict());
        mvc.perform(post("/api/reports/" + id + "/approve").with(jwt().jwt(t -> t.subject("reviewer")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isOk());
        jdbc.update("UPDATE runs SET report='# Changed [L-1]' WHERE id='review-run'");
        jdbc.update("UPDATE documents SET index_version=2 WHERE id='doc-1'");
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.report_markdown").value("# Original [L-1]"))
            .andExpect(jsonPath("$.sources[0].locator").value("第 2 页"))
            .andExpect(jsonPath("$.sources[0].original_available").value(false));
        jdbc.update("DELETE FROM chunks WHERE id='chunk-1'");
        jdbc.update("DELETE FROM documents WHERE id='doc-1'");
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.report_markdown").value("# Original [L-1]"))
            .andExpect(jsonPath("$.sources[0].text").value("证据"))
            .andExpect(jsonPath("$.sources[0].original_available").value(false));
        mvc.perform(get("/api/research/runs/review-run/report").with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNotFound());
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("outsider"))))
            .andExpect(status().isNotFound());
        mvc.perform(post("/api/reports/" + id + "/withdraw").with(jwt().jwt(t -> t.subject("reviewer")))
                .header("X-Workspace-ID", "review-space").contentType("application/json")
                .content("{\"reason\":\"引用资料已更新\"}"))
            .andExpect(status().isOk());
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNotFound());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/reports/" + id)
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNoContent());
        assertThat(count("report_publications")).isZero();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'report.%'", Integer.class))
            .isEqualTo(4);
    }

    @Test
    void reviewRejectsSelfApprovalBadQualityAndConcurrentDecisions() throws Exception {
        workspace("review-space", "author", "admin", 2);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('review-space','other-admin','admin','now')");
        run("decision-run", "review-space");
        jdbc.update("UPDATE runs SET created_by='author',status='completed',report='# Report [W-1]',sources='[{\"id\":\"W-1\",\"kind\":\"web\",\"text\":\"Evidence\"}]',validation='{\"verification_contract\":\"verification-v3-claim-span\",\"quality\":\"unverified\",\"checked_claims\":1,\"supported_claims\":1}' WHERE id='decision-run'");
        mvc.perform(post("/api/research/runs/decision-run/publication")
                .with(jwt().jwt(t -> t.subject("author"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isUnprocessableEntity());
        jdbc.update("UPDATE runs SET validation='{\"verification_contract\":\"verification-v3-claim-span\",\"quality\":\"complete\",\"checked_claims\":1,\"supported_claims\":1}' WHERE id='decision-run'");
        String id = String.valueOf(body(mvc.perform(post("/api/research/runs/decision-run/publication")
                .with(jwt().jwt(t -> t.subject("author"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isCreated()).andReturn()).get("id"));
        mvc.perform(post("/api/reports/" + id + "/approve").with(jwt().jwt(t -> t.subject("author")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isForbidden());
        List<Object> outcomes = concurrentResults(
            () -> mvc.perform(post("/api/reports/" + id + "/approve")
                .with(jwt().jwt(t -> t.subject("other-admin"))).header("X-Workspace-ID", "review-space"))
                .andReturn().getResponse().getStatus(),
            () -> mvc.perform(post("/api/reports/" + id + "/approve")
                .with(jwt().jwt(t -> t.subject("other-admin"))).header("X-Workspace-ID", "review-space"))
                .andReturn().getResponse().getStatus());
        assertThat(outcomes).containsExactlyInAnyOrder(200, 409);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_logs WHERE action='report.approve'", Integer.class)).isEqualTo(1);
    }

    @Test
    void partialReportKeepsGapsAndRequiresReviewerScope() throws Exception {
        workspace("partial-space", "author", "researcher", 2);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('partial-space','reviewer','admin','now'),('partial-space','reader','viewer','now')");
        run("partial-run", "partial-space");
        jdbc.update("""
            UPDATE runs SET created_by='author',status='completed',report='# 有限结论 [W-1]\\n\\n未回答：长期效果',
            sources='[{"id":"W-1","kind":"web","title":"研究资料","text":"已有依据"}]',
            validation='{"verification_contract":"verification-v3-claim-span","quality":"partial","checked_claims":2,"supported_claims":1,"removed_claims":1,"unanswered_questions":["长期效果"]}'
            WHERE id='partial-run'""");
        String id = String.valueOf(body(mvc.perform(post("/api/research/runs/partial-run/publication")
                .with(jwt().jwt(t -> t.subject("author"))).header("X-Workspace-ID", "partial-space"))
            .andExpect(status().isCreated()).andExpect(jsonPath("$.validation.quality").value("partial"))
            .andReturn()).get("id"));
        mvc.perform(get("/api/reports/pending").with(jwt().jwt(t -> t.subject("reviewer")))
                .header("X-Workspace-ID", "partial-space"))
            .andExpect(status().isOk()).andExpect(jsonPath("$[0].quality").value("partial"));
        mvc.perform(post("/api/reports/" + id + "/approve").with(jwt().jwt(t -> t.subject("reviewer")))
                .header("X-Workspace-ID", "partial-space"))
            .andExpect(status().isUnprocessableEntity());
        mvc.perform(post("/api/reports/" + id + "/approve").with(jwt().jwt(t -> t.subject("reviewer")))
                .header("X-Workspace-ID", "partial-space").contentType("application/json")
                .content("{\"reason\":\"仅发布已有依据，长期效果尚未回答\"}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.review_reason").value("仅发布已有依据，长期效果尚未回答"));
        mvc.perform(get("/api/reports").with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "partial-space"))
            .andExpect(status().isOk()).andExpect(jsonPath("$[0].quality").value("partial"));
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "partial-space"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.validation.unanswered_questions[0]").value("长期效果"))
            .andExpect(jsonPath("$.review_reason").value("仅发布已有依据，长期效果尚未回答"));
    }

    @Test
    void workspaceMemberManagementPreservesAnAdministrator() throws Exception {
        workspace("member-space", "owner", "admin", 2);
        mvc.perform(put("/api/workspaces/member-space/members").with(jwt().jwt(t -> t.subject("owner")))
                .header("X-Workspace-ID", "member-space").contentType("application/json")
                .content("{\"subject\":\"owner\",\"role\":\"researcher\"}"))
            .andExpect(status().isConflict());
        mvc.perform(put("/api/workspaces/member-space/members").with(jwt().jwt(t -> t.subject("owner")))
                .header("X-Workspace-ID", "member-space").contentType("application/json")
                .content("{\"subject\":\"reviewer\",\"role\":\"admin\"}"))
            .andExpect(status().isOk());
        mvc.perform(put("/api/workspaces/member-space/members").with(jwt().jwt(t -> t.subject("owner")))
                .header("X-Workspace-ID", "member-space").contentType("application/json")
                .content("{\"subject\":\"owner\",\"role\":\"researcher\"}"))
            .andExpect(status().isOk());
        mvc.perform(get("/api/workspaces/member-space/members").with(jwt().jwt(t -> t.subject("reviewer")))
                .header("X-Workspace-ID", "member-space"))
            .andExpect(status().isOk()).andExpect(jsonPath("$[?(@.subject == 'owner')].role").value("researcher"));
    }

    @Test
    void removingWorkspaceMemberRevokesAccessButKeepsPublishedReportAndAudit() throws Exception {
        workspace("remove-space", "owner", "admin", 2);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('remove-space','reviewer','admin','now'),('remove-space','reader','viewer','now')");
        run("remove-run", "remove-space");
        jdbc.update("""
            UPDATE runs SET created_by='owner',status='completed',report='# 已核验结论 [W-1]',
            sources='[{"id":"W-1","kind":"web","text":"原文证据"}]',
            validation='{"verification_contract":"verification-v3-claim-span","quality":"complete","checked_claims":1,"supported_claims":1}'
            WHERE id='remove-run'""");
        String id = String.valueOf(body(mvc.perform(post("/api/research/runs/remove-run/publication")
                .with(jwt().jwt(t -> t.subject("owner"))).header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isCreated()).andReturn()).get("id"));
        mvc.perform(post("/api/reports/" + id + "/approve").with(jwt().jwt(t -> t.subject("reviewer")))
                .header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isOk());
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isOk());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/workspaces/remove-space/members/reader")
                .with(jwt().jwt(t -> t.subject("reader"))).header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isForbidden());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/workspaces/remove-space/members/owner")
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isConflict());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/workspaces/remove-space/members/reviewer")
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isConflict());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/workspaces/remove-space/members/missing")
                .with(jwt().jwt(t -> t.subject("owner"))).header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isNotFound());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/workspaces/remove-space/members/reader")
                .with(jwt().jwt(t -> t.subject("owner"))).header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isNoContent());
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isForbidden());
        mvc.perform(get("/api/threads").with(jwt().jwt(t -> t.subject("reader")))
                .header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isForbidden());
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("owner")))
                .header("X-Workspace-ID", "remove-space"))
            .andExpect(status().isOk());
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_logs WHERE action='membership.remove'", Integer.class))
            .isEqualTo(1);
    }

    @Test
    void legacyUnknownAuthorCannotSubmitAndCrossWorkspaceCannotReadReport() throws Exception {
        workspace("legacy-space", "admin", "admin", 2);
        workspace("other-space", "other", "viewer", 2);
        run("legacy-run", "legacy-space");
        jdbc.update("""
            UPDATE runs SET created_by='',status='completed',report='# Legacy',
            sources='[{"id":"W-1","kind":"web","text":"Evidence"}]',
            validation='{"verification_contract":"verification-v3-claim-span","quality":"complete","checked_claims":1,"supported_claims":1}'
            WHERE id='legacy-run'""");
        mvc.perform(post("/api/research/runs/legacy-run/publication")
                .with(jwt().jwt(t -> t.subject("admin"))).header("X-Workspace-ID", "legacy-space"))
            .andExpect(status().isForbidden());
        mvc.perform(get("/api/research/runs/legacy-run").with(jwt().jwt(t -> t.subject("admin")))
                .header("X-Workspace-ID", "legacy-space"))
            .andExpect(status().isOk());
        mvc.perform(get("/api/research/runs/legacy-run").with(jwt().jwt(t -> t.subject("other")))
                .header("X-Workspace-ID", "other-space"))
            .andExpect(status().isNotFound());
    }

    @Test
    void rejectedReportRequiresReasonAndRemainsPrivateUntilExplicitCleanup() throws Exception {
        workspace("review-space", "author", "researcher", 2);
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('review-space','reviewer','admin','now'),('review-space','viewer','viewer','now')");
        run("rejected-run", "review-space");
        jdbc.update("""
            UPDATE runs SET created_by='author',status='completed',report='# Result [W-1]',
            sources='[{"id":"W-1","kind":"web","text":"Evidence"}]',
            validation='{"verification_contract":"verification-v3-claim-span","quality":"complete","checked_claims":1,"supported_claims":1}'
            WHERE id='rejected-run'""");
        String id = String.valueOf(body(mvc.perform(post("/api/research/runs/rejected-run/publication")
                .with(jwt().jwt(t -> t.subject("author"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isCreated()).andReturn()).get("id"));
        mvc.perform(post("/api/reports/" + id + "/reject")
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "review-space")
                .contentType("application/json").content("{\"reason\":\"   \"}"))
            .andExpect(status().isUnprocessableEntity());
        mvc.perform(post("/api/reports/" + id + "/reject")
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "review-space")
                .contentType("application/json").content("{\"reason\":\"日期证据不足\"}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.review_reason").value("日期证据不足"));
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("author")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isOk());
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("viewer")))
                .header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNotFound());
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/reports/" + id)
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isNoContent());
        assertThat(count("report_publications")).isZero();
        mvc.perform(post("/api/research/runs/rejected-run/publication")
                .with(jwt().jwt(t -> t.subject("author"))).header("X-Workspace-ID", "review-space"))
            .andExpect(status().isConflict());
    }

    @Test
    void reportRevisionIsIdempotentReviewedAndPreservesPublishedHistory() throws Exception {
        Map<String, Object> original = lifecycleReport("public", -1);
        WorkspaceIdentity writer = new WorkspaceIdentity("lifecycle", "writer", "researcher", "Writer");
        WorkspaceIdentity reviewer = new WorkspaceIdentity("lifecycle", "reviewer", "admin", "Reviewer");
        String originalId = String.valueOf(original.get("id"));
        publications.approve(reviewer, originalId, "");
        Requests.ReportRevision request = new Requests.ReportRevision("revision-request-1", String.valueOf(original.get("content_sha256")), -1);
        run("preoccupied-request", "lifecycle");
        String occupiedKey = "revision-" + java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256")
            .digest("writer:revision-request-1".getBytes(java.nio.charset.StandardCharsets.UTF_8))).substring(0, 48);
        jdbc.update("UPDATE runs SET client_request_id=?,created_by='writer',status='completed' WHERE id='preoccupied-request'", occupiedKey);
        Map<String, Object> revision = body(mvc.perform(post("/api/reports/" + originalId + "/revisions")
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle")
                .contentType("application/json").content(mapper.writeValueAsString(request)))
            .andExpect(status().isAccepted()).andReturn());
        assertThat(publications.revise(writer, originalId, request)).isEqualTo(revision);
        assertThat(count("report_revision_jobs")).isEqualTo(1);
        assertThat(count("business_outbox")).isEqualTo(1);
        String runId = String.valueOf(revision.get("run_id"));
        assertThat(runId).isNotEqualTo("preoccupied-request");
        String revisionThread = jdbc.queryForObject("SELECT thread_id FROM runs WHERE id=?", String.class, runId);
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/threads/" + revisionThread)
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isConflict());
        completeLifecycleRun(runId, "public", -1, "新版结论");
        jdbc.update("UPDATE report_publications SET content_sha256=? WHERE id=?", "f".repeat(64), originalId);
        assertThatThrownBy(() -> publications.submit(writer, runId))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("原报告指纹");
        jdbc.update("UPDATE report_publications SET content_sha256=? WHERE id=?", original.get("content_sha256"), originalId);
        Map<String, Object> updated = publications.submit(writer, runId);
        assertThat(updated.get("revision_no")).isEqualTo(2);
        assertThat(updated.get("parent_report_id")).isEqualTo(originalId);
        String updatedId = String.valueOf(updated.get("id"));
        assertThatThrownBy(() -> publications.approve(new WorkspaceIdentity("lifecycle", "writer", "admin", "Writer"), updatedId, ""))
            .isInstanceOf(ResponseStatusException.class);
        jdbc.update("UPDATE report_publications SET content_sha256=? WHERE id=?", "f".repeat(64), originalId);
        assertThatThrownBy(() -> publications.approve(reviewer, updatedId, ""))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("修订依据已变化");
        jdbc.update("UPDATE report_publications SET content_sha256=? WHERE id=?", original.get("content_sha256"), originalId);
        publications.approve(reviewer, updatedId, "");
        assertThat(publications.series(writer, String.valueOf(original.get("series_id"))).get("current_report_id")).isEqualTo(updatedId);
        assertThat(publications.get(writer, originalId).get("report_markdown")).isEqualTo("# 原结论 [W-1]");
        assertThat((List<?>) publications.diff(writer, updatedId, originalId).get("changes")).hasSize(1);
        mvc.perform(get("/api/reports/" + updatedId + "/diff?against=" + originalId)
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.changes[0].kind").value("changed"));
        mvc.perform(get("/api/reports/" + updatedId + "/impacts")
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.current_corpus_revision").value(-1));
        mvc.perform(get("/api/report-series/" + original.get("series_id"))
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.current_report_id").value(updatedId));
        publications.withdraw(reviewer, updatedId, "撤回修订");
        assertThat(publications.series(writer, String.valueOf(original.get("series_id"))).get("current_report_id")).isNull();
        assertThatThrownBy(() -> publications.revise(writer, originalId,
            new Requests.ReportRevision("revision-request-2", String.valueOf(original.get("content_sha256")), -1)))
            .isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void corpusChangesFenceSubmissionApprovalAndRevisionWhilePublicReportsRemainIndependent() throws Exception {
        Map<String, Object> original = lifecycleReport("internal", 3);
        WorkspaceIdentity writer = new WorkspaceIdentity("lifecycle", "writer", "researcher", "Writer");
        WorkspaceIdentity reviewer = new WorkspaceIdentity("lifecycle", "reviewer", "admin", "Reviewer");
        String id = String.valueOf(original.get("id"));
        jdbc.update("UPDATE workspace_corpus_revisions SET revision=4 WHERE workspace_id='lifecycle'");
        assertThatThrownBy(() -> publications.approve(reviewer, id, ""))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("资料已变化");
        assertThat(publications.get(writer, id).get("evidence_status")).isEqualTo("check_pending");
        run("stale-artifact", "lifecycle");
        completeLifecycleRun("stale-artifact", "internal", 3, "过期结论");
        assertThatThrownBy(() -> publications.submit(writer, "stale-artifact"))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("资料版本已变化");
        jdbc.update("UPDATE run_artifacts SET corpus_revision=4,attempt_count=0 WHERE run_id='stale-artifact'");
        assertThatThrownBy(() -> publications.submit(writer, "stale-artifact"))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("执行代次");
        run("public-artifact", "lifecycle");
        completeLifecycleRun("public-artifact", "public", -1, "公开结论");
        Map<String, Object> publicReport = publications.submit(writer, "public-artifact");
        publications.approve(reviewer, String.valueOf(publicReport.get("id")), "");
        assertThat(((Number) publicReport.get("corpus_revision")).longValue()).isEqualTo(-1L);
    }

    @Test
    void revisionsRejectForeignAuthorsReusedKeysAndObsoletePublishedParents() throws Exception {
        Map<String, Object> original = lifecycleReport("public", -1);
        WorkspaceIdentity writer = new WorkspaceIdentity("lifecycle", "writer", "researcher", "Writer");
        WorkspaceIdentity reviewer = new WorkspaceIdentity("lifecycle", "reviewer", "admin", "Reviewer");
        String id = String.valueOf(original.get("id"));
        publications.approve(reviewer, id, "");
        jdbc.update("INSERT INTO memberships VALUES('lifecycle','stranger','researcher','now')");
        Requests.ReportRevision first = new Requests.ReportRevision("first-revision", String.valueOf(original.get("content_sha256")), -1);
        assertThatThrownBy(() -> publications.revise(new WorkspaceIdentity("lifecycle", "stranger", "researcher", "Stranger"), id, first))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("作者或管理员");
        Map<String, Object> job = publications.revise(writer, id, first);
        Map<String, Object> competing = publications.revise(writer, id,
            new Requests.ReportRevision("competing-revision", String.valueOf(original.get("content_sha256")), -1));
        assertThatThrownBy(() -> publications.revise(writer, id,
            new Requests.ReportRevision("first-revision", "a".repeat(64), -1)))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("不同内容");
        String newRun = String.valueOf(job.get("run_id"));
        completeLifecycleRun(newRun, "public", -1, "新版");
        Map<String, Object> revised = publications.submit(writer, newRun);
        String competingRun = String.valueOf(competing.get("run_id"));
        completeLifecycleRun(competingRun, "public", -1, "另一修订");
        Map<String, Object> competingReport = publications.submit(writer, competingRun);
        publications.approve(reviewer, String.valueOf(revised.get("id")), "");
        assertThatThrownBy(() -> publications.approve(reviewer, String.valueOf(competingReport.get("id")), ""))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("旧修订不能发布");
        assertThatThrownBy(() -> publications.revise(writer, id,
            new Requests.ReportRevision("late-revision", String.valueOf(original.get("content_sha256")), -1)))
            .isInstanceOf(ResponseStatusException.class).hasMessageContaining("最新正式版本");
        mvc.perform(get("/api/reports?limit=1").with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(1))
            .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.header().exists("X-Next-Cursor"));
        mvc.perform(get("/api/reports?limit=101").with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isBadRequest());
    }

    private Map<String, Object> lifecycleReport(String policy, long revision) {
        workspace("lifecycle", "writer", "researcher", 2);
        jdbc.update("INSERT INTO memberships VALUES('lifecycle','reviewer','admin','now')");
        jdbc.update("INSERT INTO workspace_corpus_revisions VALUES('lifecycle',?)", Math.max(0, revision));
        run("lifecycle-original", "lifecycle");
        completeLifecycleRun("lifecycle-original", policy, revision, "原结论");
        return publications.submit(new WorkspaceIdentity("lifecycle", "writer", "researcher", "Writer"), "lifecycle-original");
    }

    @Test
    void reportDiffIgnoresRetrievalDiagnosticsButPreservesSourceVersionChanges() throws Exception {
        workspace("lifecycle", "writer", "researcher", 2);
        WorkspaceIdentity writer = new WorkspaceIdentity("lifecycle", "writer", "researcher", "Writer");
        jdbc.update("INSERT INTO documents VALUES('diff-doc','lifecycle','ready','hash-1',1)");
        jdbc.update("INSERT INTO chunks VALUES('diff-chunk','diff-doc','lifecycle','金额上限为500元，实习生不适用')");
        List<Map<String, Object>> reports = new ArrayList<>();
        for (int index = 0; index < 3; index++) {
            int version = index == 2 ? 2 : 1;
            String runId = "diff-run-" + index;
            jdbc.update("UPDATE documents SET index_version=?,hash=? WHERE id='diff-doc'", version, "hash-" + version);
            run(runId, "lifecycle");
            completeLifecycleRun(runId, "public", -1, "原结论");
            Map<String, Object> evidence = new java.util.LinkedHashMap<>(Map.of(
                "id", "L-1", "kind", "local", "document_id", "diff-doc", "chunk_id", "diff-chunk",
                "index_version", version, "document_hash", "hash-" + version, "locator", "第1条",
                "text", "金额上限为500元，实习生不适用", "retrieved_at", "2026-10-20T00:0" + index + ":00Z"));
            evidence.put("score", index * 0.1);
            String claim = mapper.writeValueAsString(List.of(Map.of("id", "stable-claim", "text", "金额上限为500元，实习生不适用",
                "type", "fact", "source_ids", List.of("L-1"), "evidence", List.of(evidence))));
            jdbc.update("UPDATE runs SET report='# 金额上限为500元，实习生不适用 [L-1]',sources=? WHERE id=?",
                mapper.writeValueAsString(List.of(evidence)), runId);
            jdbc.update("UPDATE run_artifacts SET claims_json=? WHERE run_id=?", claim, runId);
            Map<String, Object> report = publications.submit(writer, runId);
            if (!reports.isEmpty()) jdbc.update("UPDATE report_publications SET series_id=?,revision_no=? WHERE id=?",
                reports.getFirst().get("series_id"), index + 1, report.get("id"));
            reports.add(report);
        }
        String first = String.valueOf(reports.getFirst().get("id"));
        assertThat((List<?>) publications.diff(writer, String.valueOf(reports.get(1).get("id")), first).get("changes")).isEmpty();
        List<?> changed = (List<?>) publications.diff(writer, String.valueOf(reports.get(2).get("id")), first).get("changes");
        assertThat(changed).hasSize(1);
        assertThat(((Map<?, ?>) changed.getFirst()).get("kind")).isEqualTo("evidence_changed");
        assertThat(((Map<?, ?>) changed.getFirst()).get("old")).isInstanceOf(Map.class);
        assertThat(((Map<?, ?>) changed.getFirst()).get("new")).isInstanceOf(Map.class);
    }

    @Test
    void threadAndRunPaginationKeepEveryRecordAndConversationExportStreamsAllRuns() throws Exception {
        workspace("paging", "writer", "researcher", 2);
        for (int index = 0; index < 22; index++) {
            String id = "page-%02d".formatted(index);
            run(id, "paging");
            jdbc.update("UPDATE runs SET status='completed',created_by='writer',report=? WHERE id=?", "报告 " + id + " 中文证据", id);
            jdbc.update("UPDATE runs SET thread_id='page-00-thread' WHERE id=?", id);
        }
        jdbc.update("UPDATE threads SET created_by='writer' WHERE user_id='paging'");
        MvcResult threads = mvc.perform(get("/api/threads").with(jwt().jwt(t -> t.subject("writer")))
                .header("X-Workspace-ID", "paging"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(20)).andReturn();
        mvc.perform(get("/api/threads").param("cursor", threads.getResponse().getHeader("X-Next-Cursor"))
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "paging"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(2));
        MvcResult runs = mvc.perform(get("/api/threads/page-00-thread/runs").with(jwt().jwt(t -> t.subject("writer")))
                .header("X-Workspace-ID", "paging"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(20))
            .andExpect(jsonPath("$[0].id").value("page-21")).andReturn();
        mvc.perform(get("/api/threads/page-00-thread/runs").param("cursor", runs.getResponse().getHeader("X-Next-Cursor"))
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "paging"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(2))
            .andExpect(jsonPath("$[0].id").value("page-01"));
        MvcResult export = mvc.perform(get("/api/threads/page-00-thread/report")
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "paging"))
            .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.request().asyncStarted()).andReturn();
        String markdown = mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch(export))
            .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        assertThat(markdown).contains("报告 page-00 中文证据", "报告 page-21 中文证据", "## 22.");
    }

    @Test
    void outboxMeasuresAgeAndDeliveryDelayByCommandWithoutExposingPayloads() {
        run("observed-run", "workspace");
        SimpleMeterRegistry metrics = new SimpleMeterRegistry();
        OutboxService dispatcher = new OutboxService(jdbc, agent, properties, metrics);
        dispatcher.enqueue("observed-run", "research.schedule", "{}");
        jdbc.update("UPDATE business_outbox SET created_at=?", Instant.now().minusSeconds(12).toString());
        when(agent.internal(anyString(), anyString())).thenReturn(new AgentClient.AgentResponse(503, new HttpHeaders(), new byte[0]));
        dispatcher.dispatch();
        assertThat(metrics.get("deepresearch.business.outbox.oldest.age.seconds").gauge().value()).isGreaterThanOrEqualTo(12);
        jdbc.update("UPDATE business_outbox SET next_attempt_at=0");
        when(agent.internal(anyString(), anyString())).thenReturn(accepted());
        dispatcher.dispatch();
        assertThat(metrics.get("deepresearch.business.outbox.delivery.delay").tag("command", "research.schedule").timer().count()).isEqualTo(1);
        assertThat(metrics.get("deepresearch.business.outbox.oldest.age.seconds").gauge().value()).isZero();
    }

    @Test
    void legacyCompletedRunRequiresCurrentVerificationBeforeSubmission() throws Exception {
        workspace("lifecycle", "writer", "researcher", 2);
        run("legacy-completed", "lifecycle");
        completeLifecycleRun("legacy-completed", "public", -1, "旧核验结论");
        for (String validation : List.of(
                "{\"quality\":\"complete\",\"checked_claims\":1,\"supported_claims\":1}",
                "{\"verification_contract\":\"verification-v2\",\"quality\":\"complete\",\"checked_claims\":1,\"supported_claims\":1}")) {
            jdbc.update("UPDATE runs SET validation=? WHERE id='legacy-completed'", validation);
            MvcResult rejected = mvc.perform(post("/api/research/runs/legacy-completed/publication")
                    .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
                .andExpect(status().isConflict()).andReturn();
            assertThat(rejected.getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8)).contains("请新建研究");
            assertThat(count("report_publications")).isZero();
            assertThat(count("report_series")).isZero();
            assertThat(jdbc.queryForObject("SELECT report_submitted FROM runs WHERE id='legacy-completed'", Boolean.class)).isFalse();
        }
    }

    @Test
    void legacyPendingSnapshotCannotBeApprovedButPublishedHistoryRemainsReadable() throws Exception {
        Map<String, Object> report = lifecycleReport("public", -1);
        String id = String.valueOf(report.get("id"));
        Map<String, Object> original = jdbc.queryForMap("SELECT * FROM report_publications WHERE id=?", id);
        String legacyValidation = "{\"verification_contract\":\"verification-v2\",\"quality\":\"complete\",\"checked_claims\":1,\"supported_claims\":1}";
        String frozenDigest = java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(
            (original.get("report_markdown") + "\n" + original.get("sources_json") + "\n" + legacyValidation + "\n"
                + original.get("artifacts_json")).getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        jdbc.update("UPDATE report_publications SET validation_json=?,content_sha256=? WHERE id=?", legacyValidation, frozenDigest, id);
        MvcResult rejected = mvc.perform(post("/api/reports/" + id + "/approve")
                .with(jwt().jwt(t -> t.subject("reviewer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isConflict()).andReturn();
        assertThat(rejected.getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8)).contains("请新建研究");
        Map<String, Object> unchanged = jdbc.queryForMap("SELECT * FROM report_publications WHERE id=?", id);
        assertThat(unchanged.get("status")).isEqualTo("pending");
        assertThat(unchanged.get("reviewed_at")).isEqualTo(original.get("reviewed_at"));
        assertThat(unchanged.get("content_sha256")).isEqualTo(frozenDigest);
        assertThat(unchanged.get("validation_json")).isEqualTo(legacyValidation);
        assertThat(unchanged.get("report_markdown")).isEqualTo(original.get("report_markdown"));
        assertThat(jdbc.queryForObject("SELECT current_report_id FROM report_series WHERE id=?", String.class, report.get("series_id"))).isNull();
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM audit_logs WHERE action='report.approve'", Integer.class)).isZero();
        jdbc.update("UPDATE report_publications SET status='published' WHERE id=?", id);
        mvc.perform(get("/api/reports/" + id).with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.content_sha256").value(frozenDigest));
        mvc.perform(post("/api/research/runs/lifecycle-original/publication")
                .with(jwt().jwt(t -> t.subject("writer"))).header("X-Workspace-ID", "lifecycle"))
            .andExpect(status().isCreated()).andExpect(jsonPath("$.id").value(id));
    }

    @Test
    void migratedLegacySnapshotKeepsDigestCompatibilityAndUnknownEvidenceStatus() throws Exception {
        Map<String, Object> report = lifecycleReport("public", -1);
        String id = String.valueOf(report.get("id"));
        Map<String, Object> raw = jdbc.queryForMap("SELECT * FROM report_publications WHERE id=?", id);
        String legacyDigest = java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(
            (raw.get("report_markdown") + "\n" + raw.get("sources_json") + "\n" + raw.get("validation_json")).getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        jdbc.update("UPDATE report_publications SET artifacts_json='{}',content_sha256=?,evidence_status='undetermined' WHERE id=?", legacyDigest, id);
        Map<String, Object> approved = publications.approve(new WorkspaceIdentity("lifecycle", "reviewer", "admin", "Reviewer"), id, "");
        assertThat(approved.get("status")).isEqualTo("published");
        assertThat(approved.get("evidence_status")).isEqualTo("undetermined");
    }

    @Test
    void restrictedEvidenceRevocationGuardsFrozenReportsSeriesDiffRevisionAndDownload() throws Exception {
        Map<String,Object> report=restrictedEvidenceReport();
        String id=String.valueOf(report.get("id")), series=String.valueOf(report.get("series_id"));
        WorkspaceIdentity writer=new WorkspaceIdentity("lifecycle","writer","researcher","Writer");
        WorkspaceIdentity reviewer=new WorkspaceIdentity("lifecycle","reviewer","admin","Reviewer");
        publications.approve(reviewer,id,"");
        Map<String,Object> frozen=jdbc.queryForMap("SELECT report_markdown,sources_json,content_sha256 FROM report_publications WHERE id=?",id);
        assertThat(publications.get(writer,id).get("report_markdown")).isEqualTo("# PRIVATE-REPORT [L-1]");
        jdbc.update("INSERT INTO memories(id,user_id,kind,content,run_id,created_at) VALUES('acl-summary','lifecycle','semantic','PRIVATE-SUMMARY','acl-original','now')");
        jdbc.update("INSERT INTO memories(id,user_id,kind,content,run_id,created_at,owner_subject) VALUES('own-preference','lifecycle','preference','OWN-PREFERENCE','own-preference','now','writer')");
        mvc.perform(get("/api/memories").with(jwt().jwt(t->t.subject("writer"))).header("X-Workspace-ID","lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(2));
        jdbc.update("DELETE FROM collection_grants WHERE collection_id='private-collection' AND subject='writer'");
        mvc.perform(get("/api/memories").with(jwt().jwt(t->t.subject("writer"))).header("X-Workspace-ID","lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(1)).andExpect(jsonPath("$[0].id").value("own-preference"));
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete("/api/memories/acl-summary")
                .with(jwt().jwt(t->t.subject("writer"))).header("X-Workspace-ID","lifecycle"))
            .andExpect(status().isNotFound());
        for(String suffix:List.of("","/download","/impacts","/diff?against="+id)) {
            mvc.perform(get("/api/reports/"+id+suffix).with(jwt().jwt(t->t.subject("writer")))
                .header("X-Workspace-ID","lifecycle")).andExpect(status().isNotFound());
        }
        mvc.perform(get("/api/reports").with(jwt().jwt(t->t.subject("writer"))).header("X-Workspace-ID","lifecycle"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(0));
        mvc.perform(get("/api/report-series/"+series).with(jwt().jwt(t->t.subject("writer"))).header("X-Workspace-ID","lifecycle"))
            .andExpect(status().isNotFound());
        mvc.perform(post("/api/reports/"+id+"/revisions").with(jwt().jwt(t->t.subject("writer")))
                .header("X-Workspace-ID","lifecycle").contentType("application/json")
                .content(mapper.writeValueAsString(Map.of("client_request_id","denied-revision-01",
                    "expected_content_sha256",report.get("content_sha256"),"expected_corpus_revision",0))))
            .andExpect(status().isNotFound());
        mvc.perform(post("/api/research/runs/acl-original/publication").with(jwt().jwt(t->t.subject("writer")))
                .header("X-Workspace-ID","lifecycle")).andExpect(status().isNotFound());
        assertThat(jdbc.queryForMap("SELECT report_markdown,sources_json,content_sha256 FROM report_publications WHERE id=?",id)).isEqualTo(frozen);
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM report_revision_jobs",Integer.class)).isZero();
        // Owner and current workspace admin retain independent access; physical source deletion
        // does not erase a frozen snapshot or its retained document policy.
        assertThat(publications.get(new WorkspaceIdentity("lifecycle","doc-owner","researcher","Owner"),id).get("content_sha256")).isEqualTo(frozen.get("content_sha256"));
        jdbc.update("DELETE FROM chunks WHERE document_id='acl-document'");
        jdbc.update("DELETE FROM documents WHERE id='acl-document'");
        assertThat(publications.get(reviewer,id).get("content_sha256")).isEqualTo(frozen.get("content_sha256"));
    }

    @Test
    void collectionControlsRejectSelfPromotionAndPreserveLegacyInheritanceAndWorkspaceBoundary() throws Exception {
        workspace("acl-control","owner","admin",2);
        jdbc.update("INSERT INTO memberships VALUES('acl-control','researcher','researcher','now')");
        jdbc.update("INSERT INTO documents VALUES('legacy-document','acl-control','ready','hash',1)");
        WorkspaceIdentity researcher=new WorkspaceIdentity("acl-control","researcher","researcher","Researcher");
        assertThat(com.deepresearch.business.service.DocumentAccessService.allowed(jdbc,researcher,"legacy-document")).isTrue();
        assertThat(com.deepresearch.business.service.DocumentAccessService.allowed(jdbc,researcher,"missing-document")).isFalse();
        mvc.perform(post("/api/collections").with(jwt().jwt(t->t.subject("researcher")))
                .header("X-Workspace-ID","acl-control").contentType("application/json")
                .content("{\"name\":\"private\",\"visibility\":\"restricted\"}"))
            .andExpect(status().isForbidden());
        MvcResult collection=mvc.perform(post("/api/collections").with(jwt().jwt(t->t.subject("owner")))
                .header("X-Workspace-ID","acl-control").contentType("application/json")
                .content("{\"name\":\"private\",\"visibility\":\"restricted\"}"))
            .andExpect(status().isOk()).andReturn();
        String collectionId=String.valueOf(body(collection).get("id"));
        mvc.perform(put("/api/documents/legacy-document/collection").with(jwt().jwt(t->t.subject("owner")))
                .header("X-Workspace-ID","acl-control").contentType("application/json")
                .content(mapper.writeValueAsString(Map.of("collection_id",collectionId))))
            .andExpect(status().isOk());
        assertThat(com.deepresearch.business.service.DocumentAccessService.allowed(jdbc,researcher,"legacy-document")).isFalse();
        mvc.perform(put("/api/collections/"+collectionId+"/grants").with(jwt().jwt(t->t.subject("researcher")))
                .header("X-Workspace-ID","acl-control").contentType("application/json")
                .content("{\"subject\":\"researcher\",\"permission\":\"write\"}"))
            .andExpect(status().isForbidden());
        mvc.perform(put("/api/collections/"+collectionId+"/grants").with(jwt().jwt(t->t.subject("owner")))
                .header("X-Workspace-ID","acl-control").contentType("application/json")
                .content("{\"subject\":\"researcher\",\"permission\":\"read\"}"))
            .andExpect(status().isOk());
        assertThat(com.deepresearch.business.service.DocumentAccessService.allowed(jdbc,researcher,"legacy-document")).isTrue();
        workspace("foreign","foreign-owner","admin",2);
        jdbc.update("INSERT INTO documents VALUES('foreign-document','foreign','ready','hash',1)");
        assertThat(com.deepresearch.business.service.DocumentAccessService.allowed(jdbc,
            new WorkspaceIdentity("acl-control","owner","admin","Owner"),"foreign-document")).isFalse();
    }

    @Test
    void conversationMarkdownRechecksDocumentPermissionBeforeStreamingEachRecord() throws Exception {
        restrictedEvidenceReport();
        jdbc.update("UPDATE threads SET created_by='writer' WHERE id='acl-original-thread'");
        MvcResult allowed=mvc.perform(get("/api/threads/acl-original-thread/report")
            .with(jwt().jwt(t->t.subject("writer"))).header("X-Workspace-ID","lifecycle"))
            .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.request().asyncStarted()).andReturn();
        String report=mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch(allowed))
            .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        assertThat(report).contains("PRIVATE-REPORT");
        jdbc.update("DELETE FROM collection_grants WHERE collection_id='private-collection' AND subject='writer'");
        MvcResult denied=mvc.perform(get("/api/threads/acl-original-thread/report")
            .with(jwt().jwt(t->t.subject("writer"))).header("X-Workspace-ID","lifecycle"))
            .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.request().asyncStarted()).andReturn();
        String body=mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch(denied))
            .andExpect(status().isNotFound()).andReturn().getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        assertThat(body).doesNotContain("PRIVATE-REPORT");
    }

    private Map<String,Object> restrictedEvidenceReport() throws Exception {
        workspace("lifecycle","writer","researcher",2);
        jdbc.update("INSERT INTO memberships VALUES('lifecycle','reviewer','admin','now')");
        jdbc.update("INSERT INTO memberships VALUES('lifecycle','doc-owner','researcher','now')");
        jdbc.update("INSERT INTO workspace_corpus_revisions VALUES('lifecycle',0)");
        jdbc.update("INSERT INTO collections(id,workspace_id,name,visibility,created_by,created_at) VALUES('private-collection','lifecycle','Private','restricted','reviewer','now')");
        jdbc.update("INSERT INTO documents VALUES('acl-document','lifecycle','ready','acl-hash',1)");
        jdbc.update("INSERT INTO chunks VALUES('acl-chunk','acl-document','lifecycle','PRIVATE-EVIDENCE')");
        jdbc.update("INSERT INTO document_policies(document_id,workspace_id,collection_id,owner_subject) VALUES('acl-document','lifecycle','private-collection','doc-owner')");
        jdbc.update("INSERT INTO collection_grants VALUES('private-collection','writer','read')");
        run("acl-original","lifecycle");
        completeLifecycleRun("acl-original","internal",0,"PRIVATE-REPORT");
        String source=mapper.writeValueAsString(List.of(Map.of("id","L-1","kind","local","document_id","acl-document",
            "chunk_id","acl-chunk","document_hash","acl-hash","index_version",1,"text","PRIVATE-EVIDENCE")));
        jdbc.update("UPDATE runs SET report='# PRIVATE-REPORT [L-1]',sources=? WHERE id='acl-original'",source);
        return publications.submit(new WorkspaceIdentity("lifecycle","writer","researcher","Writer"),"acl-original");
    }

    private void completeLifecycleRun(String id, String policy, long revision, String text) {
        jdbc.update("""
            UPDATE runs SET created_by='writer',status='completed',attempt_count=1,data_policy=?,report=?,
            sources='[{"id":"W-1","kind":"web","text":"证据"}]',
            validation='{"verification_contract":"verification-v3-claim-span","quality":"complete","checked_claims":1,"supported_claims":1}' WHERE id=?""", policy, "# " + text + " [W-1]", id);
        jdbc.update("""
            INSERT INTO run_artifacts(run_id,workspace_id,attempt_count,corpus_revision,requirements_json,claims_json,created_at)
            VALUES(?,'lifecycle',1,?,'{"requirements":["研究要求"],"questions":["问题"]}',?,'now')""",
            id, revision, "[{\"id\":\"claim-1\",\"text\":\"" + text + "\",\"type\":\"fact\",\"source_ids\":[\"W-1\"]}]");
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
