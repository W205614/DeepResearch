package com.deepresearch.business.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.deepresearch.business.config.DeepResearchProperties;
import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;

class AgentClientOriginTest {
    private HttpServer agent;
    private HttpServer otherOrigin;
    private AgentClient client;
    private final AtomicInteger otherOriginRequests = new AtomicInteger();
    private final CopyOnWriteArrayList<String> agentRequests = new CopyOnWriteArrayList<>();

    @BeforeEach
    void startIsolatedOrigins() throws Exception {
        otherOrigin = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        otherOrigin.createContext("/", exchange -> {
            otherOriginRequests.incrementAndGet();
            exchange.sendResponseHeaders(204, -1);
            exchange.close();
        });
        otherOrigin.start();
        agent = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        agent.createContext("/", exchange -> {
            agentRequests.add(exchange.getRequestMethod() + " " + exchange.getRequestURI()
                + " " + exchange.getRequestHeaders().getFirst("Authorization"));
            exchange.getRequestBody().readAllBytes();
            if (exchange.getRequestURI().getPath().equals("/api/redirect")) {
                exchange.getResponseHeaders().set("Location", otherOriginUrl() + "/credential-target");
                exchange.sendResponseHeaders(302, -1);
            } else {
                byte[] body = "agent-only".getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
            }
            exchange.close();
        });
        agent.start();
        DeepResearchProperties properties = new DeepResearchProperties();
        properties.setAgentBaseUrl("http://127.0.0.1:" + agent.getAddress().getPort());
        client = new AgentClient(properties, new SimpleMeterRegistry());
    }

    @AfterEach
    void stopIsolatedOrigins() {
        if (agent != null) agent.stop(0);
        if (otherOrigin != null) otherOrigin.stop(0);
    }

    @Test
    void pathAndQueryCannotReplaceTheConfiguredOrigin() {
        String authority = "127.0.0.1:" + otherOrigin.getAddress().getPort();
        for (String path : new String[]{"/api/documents/http://" + authority + "/secret",
                "//" + authority + "/secret",
                "/api/documents//@" + authority + "/secret",
                "/api/documents/%2f%2f" + authority + "/secret"}) {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", path);
            request.setQueryString("url=http://" + authority + "/secret&value=@" + authority);
            request.addHeader("Authorization", "Bearer isolated-test-token");
            assertThat(client.forward(request, "payload".getBytes(StandardCharsets.UTF_8)).status()).isEqualTo(200);
        }
        assertThat(agentRequests).hasSize(4).allMatch(value -> value.endsWith("Bearer isolated-test-token"));
        assertThat(otherOriginRequests).hasValue(0);
    }

    @Test
    void queryAuthoritySyntaxRemainsQueryDataForTheStreamingProxy() {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/documents");
        request.setQueryString("@" + otherOriginUrl() + "/secret#//another.invalid");
        request.addHeader("Authorization", "Bearer isolated-test-token");
        request.setContent("payload".getBytes(StandardCharsets.UTF_8));
        assertThat(client.forward(request).status()).isEqualTo(200);
        assertThat(agentRequests).hasSize(1);
        assertThat(otherOriginRequests).hasValue(0);
    }

    @Test
    void redirectsNeverForwardRequestsOrCredentialsToAnotherOrigin() {
        MockHttpServletRequest streaming = new MockHttpServletRequest("POST", "/api/redirect");
        streaming.addHeader("Authorization", "Bearer isolated-test-token");
        streaming.setContent("payload".getBytes(StandardCharsets.UTF_8));
        assertThat(client.forward(streaming).status()).isEqualTo(302);
        assertThat(client.forward(streaming, new byte[0]).status()).isEqualTo(302);
        assertThat(client.get("/api/redirect").status()).isEqualTo(302);
        assertThat(agentRequests).hasSize(3);
        assertThat(otherOriginRequests).hasValue(0);
    }

    private String otherOriginUrl() {
        return "http://127.0.0.1:" + otherOrigin.getAddress().getPort();
    }
}
