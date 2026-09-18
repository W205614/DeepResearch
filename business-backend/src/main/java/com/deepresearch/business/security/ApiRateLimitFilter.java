package com.deepresearch.business.security;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import com.deepresearch.business.config.DeepResearchProperties;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Per-subject token bucket for the single-node business API. Cross-node quotas still belong in a shared gateway/Redis.
 */
@Component
public class ApiRateLimitFilter extends OncePerRequestFilter {
    private static final Logger LOG = LoggerFactory.getLogger(ApiRateLimitFilter.class);
    private static final long IDLE_NANOS = Duration.ofMinutes(10).toNanos();

    private final DeepResearchProperties properties;
    private final ConcurrentHashMap<String, Bucket> buckets = new ConcurrentHashMap<>();
    private final AtomicLong calls = new AtomicLong();
    private final Counter rejected;
    private final Counter concurrencyRejected;
    private final Semaphore requestPermits;
    private final AtomicInteger active = new AtomicInteger();
    private final AtomicLong lastConcurrencyLog = new AtomicLong();
    private final Bucket globalBucket;

    public ApiRateLimitFilter(DeepResearchProperties properties, MeterRegistry registry) {
        this.properties = properties;
        this.rejected = registry.counter("deepresearch.business.rate_limit.rejected");
        this.concurrencyRejected = registry.counter("deepresearch.business.concurrency.rejected");
        this.requestPermits = new Semaphore(Math.max(1, properties.getApiMaxConcurrent()), true);
        this.globalBucket = new Bucket(Math.max(1, properties.getApiGlobalRateLimitPerSecond()),
            Math.max(1, properties.getApiGlobalRateLimitBurst()), System.nanoTime());
        registry.gauge("deepresearch.business.rate_limit.principals", buckets, ConcurrentHashMap::size);
        registry.gauge("deepresearch.business.concurrency.active", active);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return !path.startsWith("/api/") || path.equals("/api/auth/config");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (!(authentication instanceof JwtAuthenticationToken token) || !authentication.isAuthenticated()) {
            chain.doFilter(request, response);
            return;
        }
        long now = System.nanoTime();
        cleanupOccasionally(now);
        // Never key on the caller-supplied workspace header: an unauthorized value could bypass the bucket.
        String key = token.getToken().getSubject();
        Bucket bucket = buckets.get(key);
        if (bucket == null && buckets.size() >= Math.max(1, properties.getApiRateLimitMaxPrincipals())) {
            reject(response, key, "principal_capacity");
            return;
        }
        bucket = buckets.computeIfAbsent(key, ignored -> new Bucket(
            Math.max(1, properties.getApiRateLimitPerSecond()), Math.max(1, properties.getApiRateLimitBurst()), now));
        if (!bucket.tryConsume(now)) {
            reject(response, key, "rate");
            return;
        }
        if (!globalBucket.tryConsume(now)) {
            reject(response, key, "global_rate");
            return;
        }
        boolean acquired;
        try {
            acquired = requestPermits.tryAcquire(
                Math.max(0, properties.getApiConcurrencyAcquireTimeoutMillis()), TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            rejectConcurrency(response);
            return;
        }
        if (!acquired) {
            rejectConcurrency(response);
            return;
        }
        active.incrementAndGet();
        try {
            chain.doFilter(request, response);
        } finally {
            active.decrementAndGet();
            requestPermits.release();
        }
    }

    private void reject(HttpServletResponse response, String key, String reason) throws IOException {
        rejected.increment();
        Bucket bucket = buckets.get(key);
        if (bucket == null || bucket.shouldLog(System.nanoTime())) {
            LOG.warn("component=api_rate_limit phase=rejected reason={}", reason);
        }
        response.setStatus(429);
        response.setHeader("Retry-After", "1");
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType("application/json");
        response.getWriter().write("{\"detail\":\"请求过于频繁，请稍后重试\"}");
    }

    private void rejectConcurrency(HttpServletResponse response) throws IOException {
        concurrencyRejected.increment();
        long now = System.nanoTime();
        long previous = lastConcurrencyLog.get();
        if (now - previous >= Duration.ofSeconds(10).toNanos()
                && lastConcurrencyLog.compareAndSet(previous, now)) {
            LOG.warn("component=api_concurrency phase=rejected active={}", active.get());
        }
        response.setStatus(503);
        response.setHeader("Retry-After", "1");
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType("application/json");
        response.getWriter().write("{\"detail\":\"服务繁忙，请稍后重试\"}");
    }

    private void cleanupOccasionally(long now) {
        if ((calls.incrementAndGet() & 1023) != 0) return;
        buckets.entrySet().removeIf(entry -> now - entry.getValue().lastSeen() > IDLE_NANOS);
    }

    static final class Bucket {
        private final double refillPerNano;
        private final int capacity;
        private double tokens;
        private long lastRefill;
        private long lastSeen;
        private long lastLog;

        Bucket(int perSecond, int capacity, long now) {
            this.refillPerNano = perSecond / 1_000_000_000d;
            this.capacity = capacity;
            this.tokens = capacity;
            this.lastRefill = now;
            this.lastSeen = now;
        }

        synchronized boolean tryConsume(long now) {
            tokens = Math.min(capacity, tokens + Math.max(0, now - lastRefill) * refillPerNano);
            lastRefill = now;
            lastSeen = now;
            if (tokens < 1) return false;
            tokens -= 1;
            return true;
        }

        synchronized boolean shouldLog(long now) {
            if (now - lastLog < Duration.ofSeconds(10).toNanos()) return false;
            lastLog = now;
            return true;
        }

        synchronized long lastSeen() { return lastSeen; }
    }
}
