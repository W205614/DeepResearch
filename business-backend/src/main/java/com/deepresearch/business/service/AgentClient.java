package com.deepresearch.business.service;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import com.deepresearch.business.config.DeepResearchProperties;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
public class AgentClient {
    private static final Logger LOG = LoggerFactory.getLogger(AgentClient.class);
    private static final List<String> FORWARDED_REQUEST_HEADERS = List.of(
        "Authorization", "X-Workspace-ID", "Last-Event-ID", "X-Confirm-Delete", "Content-Type", "Accept");
    private static final List<String> FORWARDED_RESPONSE_HEADERS = List.of(
        "content-type", "content-disposition", "cache-control");

    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    private final DeepResearchProperties properties;
    private final Semaphore proxyPermits;
    private final AtomicInteger proxyActive = new AtomicInteger();
    private final AtomicLong lastBulkheadLog = new AtomicLong();
    private final AtomicLong lastCircuitLog = new AtomicLong();
    private final CircuitBreaker proxyCircuit;
    private final Counter bulkheadRejected;
    private final Counter circuitRejected;
    private final Counter upstreamFailures;
    private final Timer proxyLatency;

    public AgentClient(DeepResearchProperties properties, MeterRegistry registry) {
        this.properties = properties;
        this.proxyPermits = new Semaphore(Math.max(1, properties.getAgentProxyMaxConcurrent()), true);
        this.proxyCircuit = new CircuitBreaker(Math.max(1, properties.getAgentProxyCircuitFailureThreshold()),
            Math.max(1, properties.getAgentProxyCircuitOpenSeconds()));
        this.bulkheadRejected = registry.counter("deepresearch.business.agent.rejected", "reason", "bulkhead");
        this.circuitRejected = registry.counter("deepresearch.business.agent.rejected", "reason", "circuit_open");
        this.upstreamFailures = registry.counter("deepresearch.business.agent.calls", "result", "failure");
        this.proxyLatency = registry.timer("deepresearch.business.agent.duration", "kind", "proxy");
        registry.gauge("deepresearch.business.agent.active", proxyActive);
    }

    /** Stream proxy bodies after a permit is held, avoiding a second full upload copy in the JVM heap. */
    public AgentResponse forward(HttpServletRequest source) {
        return guardedProxy(source.getRequestURI(), () -> {
            HttpRequest.BodyPublisher body = hasBody(source)
                ? HttpRequest.BodyPublishers.ofInputStream(() -> inputStream(source))
                : HttpRequest.BodyPublishers.noBody();
            return send(buildForwardRequest(source, body), true);
        });
    }

    public AgentResponse forward(HttpServletRequest source, byte[] body) {
        return guardedProxy(source.getRequestURI(), () -> send(buildForwardRequest(source,
            body.length == 0 ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofByteArray(body)), true));
    }

    public AgentResponse get(String path) {
        return send(HttpRequest.newBuilder(uri(path, null)).GET().timeout(Duration.ofSeconds(5)).build(), false);
    }

    public AgentResponse internal(String path, String json) {
        String token = properties.resolvedInternalToken();
        if (token.isBlank()) throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "内部服务令牌未配置");
        HttpRequest request = HttpRequest.newBuilder(uri(path, null))
            .timeout(Duration.ofSeconds(10))
            .header("Authorization", "Bearer " + token)
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();
        return send(request, false);
    }

    private HttpRequest buildForwardRequest(HttpServletRequest source, HttpRequest.BodyPublisher body) {
        HttpRequest.Builder request = HttpRequest.newBuilder(uri(source.getRequestURI(), source.getQueryString()))
            .timeout(Duration.ofSeconds(Math.max(1, properties.getAgentProxyTimeoutSeconds())));
        FORWARDED_REQUEST_HEADERS.forEach(name -> {
            String value = source.getHeader(name);
            if (value != null && !value.isBlank()) request.header(name, value);
        });
        return request.method(source.getMethod(), body).build();
    }

    private AgentResponse guardedProxy(String path, RequestCall call) {
        if (!proxyCircuit.allowRequest()) {
            circuitRejected.increment();
            sampledWarning(lastCircuitLog, "component=agent_proxy phase=circuit_open path={} active={}", path);
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Agent 服务熔断中，请稍后重试");
        }
        boolean acquired;
        try {
            acquired = proxyPermits.tryAcquire(Math.max(0, properties.getAgentProxyAcquireTimeoutMillis()), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Agent 服务调用被中断", interrupted);
        }
        if (!acquired) {
            proxyCircuit.cancelProbe();
            bulkheadRejected.increment();
            sampledWarning(lastBulkheadLog, "component=agent_proxy phase=bulkhead_reject path={} active={}", path);
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Agent 代理繁忙，请稍后重试");
        }
        proxyActive.incrementAndGet();
        Timer.Sample sample = Timer.start();
        try {
            AgentResponse response = call.execute();
            if (retryableStatus(response.status())) proxyCircuit.failure();
            else proxyCircuit.success();
            return response;
        } catch (RuntimeException error) {
            upstreamFailures.increment();
            proxyCircuit.failure();
            LOG.warn("component=agent_proxy phase=failed path={} category={}", path, errorCategory(error));
            throw error;
        } finally {
            sample.stop(proxyLatency);
            proxyActive.decrementAndGet();
            proxyPermits.release();
        }
    }

    private AgentResponse send(HttpRequest request, boolean countFailure) {
        try {
            HttpResponse<byte[]> response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
            HttpHeaders headers = new HttpHeaders();
            for (String name : FORWARDED_RESPONSE_HEADERS) {
                response.headers().firstValue(name).ifPresent(value -> headers.add(name, value));
            }
            if (countFailure && retryableStatus(response.statusCode())) upstreamFailures.increment();
            return new AgentResponse(response.statusCode(), headers, response.body());
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Agent 服务调用被中断", interrupted);
        } catch (IOException | UncheckedIOException error) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Agent 服务暂不可用", error);
        }
    }

    private static InputStream inputStream(HttpServletRequest source) {
        try {
            return source.getInputStream();
        } catch (IOException error) {
            throw new UncheckedIOException(error);
        }
    }

    private static boolean hasBody(HttpServletRequest source) {
        return source.getContentLengthLong() != 0 && !List.of("GET", "HEAD", "DELETE").contains(source.getMethod());
    }

    private static boolean retryableStatus(int status) {
        return status == 429 || status == 500 || status == 502 || status == 503 || status == 504;
    }

    private static String errorCategory(RuntimeException error) {
        if (error instanceof ResponseStatusException status) return "http_" + status.getStatusCode().value();
        return error.getClass().getSimpleName();
    }

    private void sampledWarning(AtomicLong lastLog, String message, String path) {
        long now = System.nanoTime();
        long previous = lastLog.get();
        if (now - previous >= Duration.ofSeconds(10).toNanos() && lastLog.compareAndSet(previous, now)) {
            LOG.warn(message, path, proxyActive.get());
        }
    }

    private URI uri(String path, String query) {
        String base = properties.getAgentBaseUrl().replaceAll("/+$", "");
        return URI.create(base + path + (query == null || query.isBlank() ? "" : "?" + query));
    }

    private interface RequestCall { AgentResponse execute(); }

    static final class CircuitBreaker {
        private final int threshold;
        private final long openMillis;
        private final AtomicInteger failures = new AtomicInteger();
        private final AtomicLong openUntil = new AtomicLong();
        private final AtomicBoolean probe = new AtomicBoolean();

        CircuitBreaker(int threshold, long openSeconds) {
            this.threshold = threshold;
            this.openMillis = TimeUnit.SECONDS.toMillis(openSeconds);
        }

        boolean allowRequest() {
            long until = openUntil.get();
            if (until == 0) return true;
            long now = System.currentTimeMillis();
            if (now < until) return false;
            return probe.compareAndSet(false, true);
        }

        void success() {
            failures.set(0);
            openUntil.set(0);
            probe.set(false);
        }

        void failure() {
            if (failures.incrementAndGet() >= threshold) openUntil.set(System.currentTimeMillis() + openMillis);
            probe.set(false);
        }

        void cancelProbe() { probe.set(false); }
    }

    public record AgentResponse(int status, HttpHeaders headers, byte[] body) {
        public boolean successful() { return status >= 200 && status < 300; }
        public Map<String, List<String>> headerMap() { return headers; }
    }
}
