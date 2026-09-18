package com.deepresearch.business.service;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class AgentClientReliabilityTest {
    @Test
    void circuitOpensAndAllowsOnlyOneRecoveryProbe() throws Exception {
        AgentClient.CircuitBreaker circuit = new AgentClient.CircuitBreaker(2, 1);
        assertThat(circuit.allowRequest()).isTrue();
        circuit.failure();
        assertThat(circuit.allowRequest()).isTrue();
        circuit.failure();
        assertThat(circuit.allowRequest()).isFalse();

        Thread.sleep(1050);
        assertThat(circuit.allowRequest()).isTrue();
        assertThat(circuit.allowRequest()).isFalse();
        circuit.success();
        assertThat(circuit.allowRequest()).isTrue();
    }
}
