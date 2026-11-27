package com.deepresearch.business;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

import org.eclipse.jetty.server.NetworkConnectionLimit;
import org.eclipse.jetty.server.ServerConnector;
import org.eclipse.jetty.util.thread.QueuedThreadPool;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.jetty.JettyWebServer;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.web.server.servlet.context.ServletWebServerApplicationContext;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "server.address=127.0.0.1",
        "SERVER_MAX_THREADS=80",
        "SERVER_MIN_SPARE_THREADS=10",
        "SERVER_ACCEPT_COUNT=120",
        "SERVER_MAX_CONNECTIONS=240",
        "SERVER_CONNECTION_TIMEOUT=6s",
        "OUTBOX_DISPATCH_INITIAL_DELAY_MS=3600000"
})
class JettyHttpIntegrationTest {
    @Autowired
    ServletWebServerApplicationContext context;

    @Test
    void servesActualHttpWithTheConfiguredConnectorLimits() throws Exception {
        var webServer = (JettyWebServer) context.getWebServer();
        var server = webServer.getServer();
        var connector = (ServerConnector) server.getConnectors()[0];
        var threads = (QueuedThreadPool) server.getThreadPool();

        assertThat(connector.getAcceptQueueSize()).isEqualTo(120);
        assertThat(connector.getIdleTimeout()).isEqualTo(6000);
        assertThat(server.getBean(NetworkConnectionLimit.class).getMaxNetworkConnectionCount()).isEqualTo(240);
        assertThat(threads.getMinThreads()).isEqualTo(10);
        assertThat(threads.getMaxThreads()).isEqualTo(80);

        var origin = URI.create("http://127.0.0.1:" + webServer.getPort());
        var request = HttpRequest.newBuilder().timeout(Duration.ofSeconds(5));
        try (var client = HttpClient.newHttpClient()) {
            var live = client.send(request.uri(origin.resolve("/healthz")).build(), HttpResponse.BodyHandlers.ofString());
            assertThat(live.statusCode()).isEqualTo(200);
            assertThat(live.body()).isEqualTo("{\"status\":\"ok\"}");

            var metrics = client.send(request.uri(origin.resolve("/metrics")).build(), HttpResponse.BodyHandlers.ofString());
            assertThat(metrics.statusCode()).isEqualTo(200);
            assertThat(metrics.body()).contains("jetty_threads_config_max");
        }
    }
}
