package com.deepresearch.business.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.deepresearch.business.config.DeepResearchProperties;
import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.util.TreeMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
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
                "/api/documents//@" + authority + "/secret",
                "/api/documents/%2f%2f" + authority + "/secret"}) {
            MockHttpServletRequest request = new MockHttpServletRequest("POST", path);
            request.setQueryString("url=http://" + authority + "/secret&value=@" + authority);
            request.addHeader("Authorization", "Bearer isolated-test-token");
            assertThat(client.forward(request, "payload".getBytes(StandardCharsets.UTF_8)).status()).isEqualTo(200);
        }
        assertThat(agentRequests).hasSize(3).allMatch(value -> value.endsWith("Bearer isolated-test-token"));
        assertThat(otherOriginRequests).hasValue(0);
    }

    @Test
    void authorityLookingPathIsSentToTheConfiguredAgentSocket() throws Exception {
        // Newer HttpServer versions reject a leading // before invoking a handler.
        // Capture the actual HTTP request instead of assuming that parser accepts it.
        try (var worker = Executors.newSingleThreadExecutor();
             var listener = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) {
            listener.setSoTimeout(5000);
            var capture = worker.submit(() -> {
                try (var socket = listener.accept()) {
                    socket.setSoTimeout(5000);
                    var input = new BufferedReader(new InputStreamReader(
                        socket.getInputStream(), StandardCharsets.ISO_8859_1));
                    var observed = new TreeMap<String, String>(String.CASE_INSENSITIVE_ORDER);
                    observed.put("request-line", input.readLine());
                    for (String header = input.readLine(); header != null && !header.isEmpty(); header = input.readLine()) {
                        int colon = header.indexOf(':');
                        assertThat(colon).isPositive();
                        observed.put(header.substring(0, colon), header.substring(colon + 1).strip());
                    }
                    assertThat(observed.get("Content-Length")).isEqualTo("7");
                    for (char value : "payload".toCharArray()) assertThat(input.read()).isEqualTo((int) value);
                    socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Length: 10\r\n"
                        + "Connection: close\r\n\r\nagent-only").getBytes(StandardCharsets.US_ASCII));
                    socket.getOutputStream().flush();
                    return observed;
                }
            });
            String authority = "127.0.0.1:" + otherOrigin.getAddress().getPort();
            String path = "//" + authority + "/secret";
            String query = "url=http://" + authority + "/secret&value=@" + authority;
            var properties = new DeepResearchProperties();
            properties.setAgentBaseUrl("http://127.0.0.1:" + listener.getLocalPort());
            var socketClient = new AgentClient(properties, new SimpleMeterRegistry());
            var request = new MockHttpServletRequest("POST", path);
            request.setQueryString(query);
            request.addHeader("Authorization", "Bearer isolated-test-token");
            var response = socketClient.forward(request, "payload".getBytes(StandardCharsets.UTF_8));
            assertThat(response.status()).isEqualTo(200);
            assertThat(response.body()).isEqualTo("agent-only".getBytes(StandardCharsets.US_ASCII));
            var observed = capture.get(5, TimeUnit.SECONDS);
            assertThat(observed.get("request-line")).isEqualTo("POST " + path + "?" + query + " HTTP/1.1");
            assertThat(observed.get("Host")).isEqualTo("127.0.0.1:" + listener.getLocalPort());
            assertThat(observed.get("Authorization")).isEqualTo("Bearer isolated-test-token");
            assertThat(agentRequests).isEmpty();
            assertThat(otherOriginRequests).hasValue(0);
        }
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
