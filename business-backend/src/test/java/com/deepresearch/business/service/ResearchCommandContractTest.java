package com.deepresearch.business.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

class ResearchCommandContractTest {
    @Test
    @SuppressWarnings("unchecked")
    void javaAndPythonConsumeTheSameFrozenWireCases() throws Exception {
        Map<String, Object> fixture = new ObjectMapper().readValue(Files.readString(
            Path.of("..", "backend", "core", "research_command_contract.json")), Map.class);
        assertThat(fixture.get("contract_version")).isEqualTo(ResearchCommandContract.VERSION);
        for (Map<String, Object> test : (List<Map<String, Object>>) fixture.get("schedule_cases")) {
            Map<String, Object> payload = (Map<String, Object>) test.get("payload");
            assertThat(ResearchCommandContract.valid(payload)).as(String.valueOf(test.get("name")))
                .isEqualTo(test.get("valid"));
        }
        assertThat(ResearchCommandContract.valid(ResearchCommandContract.schedule(true, 3))).isTrue();
        Map<String, Object> limits = (Map<String, Object>) fixture.get("resume_fixture_limits");
        for (Map<String, Object> test : (List<Map<String, Object>>) fixture.get("resume_cases")) {
            assertThat(ResearchCommandContract.canResume(String.valueOf(test.get("status")), test.get("retryable"),
                number(test.get("deadline")), number(limits.get("epoch")), number(test.get("calls")),
                number(test.get("tokens")), number(limits.get("max_calls")), number(limits.get("max_tokens"))))
                .as(String.valueOf(test.get("name"))).isEqualTo(test.get("expected"));
        }
    }

    private long number(Object value) { return ((Number) value).longValue(); }
}
