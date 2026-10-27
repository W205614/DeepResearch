package com.deepresearch.business;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import java.time.Instant;
import java.util.Map;
import com.deepresearch.business.service.AgentClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.jdbc.Sql;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.http.MediaType;
import org.testcontainers.junit.jupiter.*;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

@SpringBootTest(properties={"deepresearch.outbox-dispatch-initial-delay-ms=3600000"})
@AutoConfigureMockMvc @Testcontainers
@Sql(scripts="/business-test-schema.sql",executionPhase=Sql.ExecutionPhase.BEFORE_TEST_METHOD)
class ResearchIntakeIntegrationTest {
    @Container static final PostgreSQLContainer PG=new PostgreSQLContainer("postgres:17.6-alpine");
    @DynamicPropertySource static void properties(DynamicPropertyRegistry r){r.add("spring.datasource.url",PG::getJdbcUrl);r.add("spring.datasource.username",PG::getUsername);r.add("spring.datasource.password",PG::getPassword);r.add("spring.security.oauth2.resourceserver.jwt.jwk-set-uri",()->"http://127.0.0.1:1/jwks");}
    @Autowired MockMvc mvc;@Autowired JdbcTemplate jdbc;@Autowired ObjectMapper mapper;
    @MockitoBean AgentClient agent;
    @BeforeEach void seed(){
        jdbc.update("INSERT INTO workspaces(id,name,created_by,created_at) VALUES('scope-space','scope','author',?)",Instant.now().toString());
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('scope-space','author','admin',?)",Instant.now().toString());
        jdbc.update("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES('scope-space','reader','viewer',?)",Instant.now().toString());
        jdbc.update("INSERT INTO workspace_limits(workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit) VALUES('scope-space',0,0,2)");
    }
    private String request(String topic,String key){return mapper.writeValueAsString(Map.of("topic",topic,"mode","deep","data_policy","public","client_request_id",key));}
    private String preflight(String key)throws Exception{
        String body=mvc.perform(post("/api/research/intakes").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(request("全面研究行业趋势",key))).andExpect(status().isOk()).andExpect(jsonPath("$.required").value(true)).andReturn().getResponse().getContentAsString();
        return mapper.readTree(body).get("id").asString();
    }
    private String answers(){return mapper.writeValueAsString(Map.of("time","2024—2026年","region","中国","object","中小企业AI应用","deliverable","比较价值与限制，明确证据缺口"));}
    @Test void broadResearchNeedsConfirmationAndReplayCreatesOneRun()throws Exception{
        mvc.perform(post("/api/research/runs").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(request("全面研究行业趋势","scope-request-01"))).andExpect(status().isConflict());
        assertThat(jdbc.queryForObject("SELECT count(*) FROM runs",Integer.class)).isZero();
        String id=preflight("scope-request-01");
        assertThat(jdbc.queryForObject("SELECT count(*) FROM business_outbox",Integer.class)).isZero();
        mvc.perform(put("/api/research/intakes/"+id).with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(answers())).andExpect(status().isOk());
        for(int n=0;n<2;n++)mvc.perform(post("/api/research/intakes/"+id+"/confirm").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(answers())).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("queued"));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM runs",Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT topic FROM runs",String.class)).contains("全面研究行业趋势","中国","交付标准");
    }
    @Test void cancelledExpiredAndReadOnlyIntakesDoNotSchedule()throws Exception{
        String cancelled=preflight("scope-cancelled-01");
        mvc.perform(post("/api/research/intakes/"+cancelled+"/cancel").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space")).andExpect(status().isOk());
        mvc.perform(post("/api/research/intakes/"+cancelled+"/confirm").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(answers())).andExpect(status().isConflict());
        String expired=preflight("scope-expired-01");jdbc.update("UPDATE research_intakes SET expires_at=1 WHERE id=?",expired);
        mvc.perform(post("/api/research/intakes/"+expired+"/confirm").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(answers())).andExpect(status().isConflict());
        mvc.perform(post("/api/research/intakes").with(jwt().jwt(j->j.subject("reader"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(request("全面研究行业趋势","readonly-scope-01"))).andExpect(status().isForbidden());
        assertThat(jdbc.queryForObject("SELECT count(*) FROM runs",Integer.class)).isZero();
    }
    @Test void preciseQuestionDoesNotRequireScopeRoundtrip()throws Exception{
        mvc.perform(post("/api/research/intakes").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(request("Python pathlib.Path.name返回什么","precise-request-01"))).andExpect(status().isOk()).andExpect(jsonPath("$.required").value(false));
        mvc.perform(post("/api/research/runs").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(request("Python pathlib.Path.name返回什么","precise-request-01"))).andExpect(status().isAccepted());
    }
    @Test void repeatedIntakeKeyCannotDisplayAnotherTopicOrChangeConfirmedMode()throws Exception{
        String id=preflight("scope-conflict-01");
        mvc.perform(post("/api/research/intakes").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(request("全面分析另一项市场前景","scope-conflict-01"))).andExpect(status().isConflict());
        jdbc.update("UPDATE research_intakes SET status='confirmed',answers_json=? WHERE id=?",answers(),id);
        var row=jdbc.queryForMap("SELECT * FROM research_intakes WHERE id=?",id);
        var tampered=com.deepresearch.business.service.ResearchIntakePolicy.confirmedRequest(mapper,row);tampered.setMode("quick");
        mvc.perform(post("/api/research/runs").with(jwt().jwt(j->j.subject("author"))).header("X-Workspace-ID","scope-space").contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsString(tampered))).andExpect(status().isConflict());
        assertThat(jdbc.queryForObject("SELECT count(*) FROM runs",Integer.class)).isZero();
    }
}
