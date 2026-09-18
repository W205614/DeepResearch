package com.deepresearch.business.service;

import static com.deepresearch.business.security.WorkspaceAccess.now;
import static com.deepresearch.business.security.WorkspaceAccess.uid;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

import com.deepresearch.business.config.DeepResearchProperties;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Tags;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
public class OutboxService {
    private static final String DELIVERED = "delivered";
    private static final String DEAD = "dead";

    private final JdbcTemplate jdbc;
    private final AgentClient agent;
    private final DeepResearchProperties properties;
    private final String leaseOwner = UUID.randomUUID().toString();
    private final Counter delivered;
    private final Counter retried;
    private final Counter dead;
    private final AtomicInteger pendingDepth = new AtomicInteger();
    private final AtomicInteger processingDepth = new AtomicInteger();
    private final AtomicInteger deadDepth = new AtomicInteger();

    public OutboxService(JdbcTemplate jdbc, AgentClient agent, DeepResearchProperties properties,
                         MeterRegistry registry) {
        this.jdbc = jdbc;
        this.agent = agent;
        this.properties = properties;
        this.delivered = registry.counter("deepresearch.business.outbox.delivery", "result", DELIVERED);
        this.retried = registry.counter("deepresearch.business.outbox.delivery", "result", "retry");
        this.dead = registry.counter("deepresearch.business.outbox.delivery", "result", DEAD);
        registry.gauge("deepresearch.business.outbox.depth", Tags.of("status", "pending"), pendingDepth);
        registry.gauge("deepresearch.business.outbox.depth", Tags.of("status", "processing"), processingDepth);
        registry.gauge("deepresearch.business.outbox.depth", Tags.of("status", "dead"), deadDepth);
    }

    public void enqueue(String runId, String eventType, String payload) {
        jdbc.update("""
            INSERT INTO business_outbox(id,aggregate_id,event_type,payload,status,attempts,next_attempt_at,created_at,updated_at)
            VALUES(?,?,?,?,'pending',0,0,?,?)
            ON CONFLICT (aggregate_id,event_type) WHERE status IN ('pending','processing') DO NOTHING""",
            uid(), runId, eventType, payload, now(), now());
    }

    @Scheduled(
        fixedDelayString = "${deepresearch.outbox-dispatch-delay-ms:500}",
        initialDelayString = "${deepresearch.outbox-dispatch-initial-delay-ms:500}"
    )
    public void dispatch() {
        expireExhaustedLeases();
        for (int index = 0; index < Math.max(1, properties.getOutboxBatchSize()); index++) {
            List<Map<String, Object>> rows = claimNext();
            if (rows.isEmpty()) break;
            deliver(rows.getFirst());
        }
        refreshDepths();
    }

    public List<Map<String, Object>> deadLetters(String workspaceId) {
        return jdbc.queryForList("""
            SELECT o.id,o.aggregate_id,o.event_type,o.attempts,o.last_error,o.created_at,o.updated_at
            FROM business_outbox o JOIN runs r ON r.id=o.aggregate_id
            WHERE r.user_id=? AND o.status='dead' ORDER BY o.updated_at DESC""", workspaceId);
    }

    public boolean retryDeadLetter(String messageId, String workspaceId) {
        return jdbc.update("""
            UPDATE business_outbox o SET status='pending',attempts=0,next_attempt_at=0,
                lease_owner='',lease_until=0,last_error='',updated_at=?
            FROM runs r WHERE o.id=? AND o.aggregate_id=r.id AND r.user_id=? AND o.status='dead'
              AND NOT EXISTS(
                SELECT 1 FROM business_outbox active
                WHERE active.id<>o.id AND active.aggregate_id=o.aggregate_id
                  AND active.event_type=o.event_type AND active.status IN ('pending','processing'))""",
            now(), messageId, workspaceId) == 1;
    }

    private List<Map<String, Object>> claimNext() {
        long epoch = Instant.now().getEpochSecond();
        long leaseUntil = epoch + Math.max(15, properties.getOutboxLeaseSeconds());
        return jdbc.queryForList("""
            WITH claimable AS (
                SELECT id FROM business_outbox
                WHERE ((status='pending' AND next_attempt_at<=?)
                    OR (status='processing' AND lease_until<=?))
                  AND attempts<?
                ORDER BY created_at,id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE business_outbox o
            SET status='processing',attempts=o.attempts+1,lease_owner=?,lease_until=?,updated_at=?
            FROM claimable c WHERE o.id=c.id
            RETURNING o.*""", epoch, epoch, Math.max(1, properties.getOutboxMaxAttempts()),
            leaseOwner, leaseUntil, now());
    }

    private void expireExhaustedLeases() {
        long epoch = Instant.now().getEpochSecond();
        int changed = jdbc.update("""
            UPDATE business_outbox SET status='dead',lease_owner='',lease_until=0,
                last_error=CASE WHEN last_error='' THEN 'retry_exhausted_after_lease_expiry' ELSE last_error END,
                updated_at=?
            WHERE attempts>=? AND (status='pending' OR (status='processing' AND lease_until<=?))""",
            now(), Math.max(1, properties.getOutboxMaxAttempts()), epoch);
        for (int index = 0; index < changed; index++) dead.increment();
    }

    private void refreshDepths() {
        pendingDepth.set(0);
        processingDepth.set(0);
        deadDepth.set(0);
        for (Map<String, Object> row : jdbc.queryForList("""
            SELECT status,COUNT(*) AS total FROM business_outbox
            WHERE status IN ('pending','processing','dead') GROUP BY status""")) {
            int total = ((Number) row.get("total")).intValue();
            switch (String.valueOf(row.get("status"))) {
                case "pending" -> pendingDepth.set(total);
                case "processing" -> processingDepth.set(total);
                case "dead" -> deadDepth.set(total);
                default -> { }
            }
        }
    }

    private void deliver(Map<String, Object> row) {
        String id = String.valueOf(row.get("id"));
        String runId = String.valueOf(row.get("aggregate_id"));
        String type = String.valueOf(row.get("event_type"));
        String payload = String.valueOf(row.get("payload"));
        try {
            AgentClient.AgentResponse response = switch (type) {
                case "research.schedule", "research.resume" ->
                    agent.internal("/internal/agent/runs/" + runId + "/schedule", payload);
                case "research.abort" -> agent.internal("/internal/agent/runs/" + runId + "/abort", "{}");
                default -> throw new DeliveryException("unknown_event", false);
            };
            if (!response.successful()) {
                boolean retryable = response.status() == HttpStatus.REQUEST_TIMEOUT.value()
                    || response.status() == HttpStatus.TOO_MANY_REQUESTS.value() || response.status() >= 500;
                throw new DeliveryException("agent_http_" + response.status(), retryable);
            }
            int changed = jdbc.update("""
                UPDATE business_outbox SET status='delivered',lease_owner='',lease_until=0,
                    delivered_at=?,last_error='',updated_at=?
                WHERE id=? AND status='processing' AND lease_owner=?""", now(), now(), id, leaseOwner);
            if (changed == 1) delivered.increment();
        } catch (RuntimeException error) {
            fail(row, id, error);
        }
    }

    private void fail(Map<String, Object> row, String id, RuntimeException error) {
        int attempts = ((Number) row.get("attempts")).intValue();
        boolean retryable = !(error instanceof DeliveryException delivery) || delivery.retryable;
        boolean exhausted = attempts >= Math.max(1, properties.getOutboxMaxAttempts());
        String code = errorCode(error);
        if (!retryable || exhausted) {
            int changed = jdbc.update("""
                UPDATE business_outbox SET status='dead',lease_owner='',lease_until=0,last_error=?,updated_at=?
                WHERE id=? AND status='processing' AND lease_owner=?""", code, now(), id, leaseOwner);
            if (changed == 1) dead.increment();
            return;
        }
        long exponent = Math.max(0, Math.min(20, attempts - 1));
        long delay = Math.min(Math.max(1, properties.getOutboxMaxDelaySeconds()),
            Math.max(1, properties.getOutboxBaseDelaySeconds()) * (1L << exponent));
        int changed = jdbc.update("""
            UPDATE business_outbox SET status='pending',next_attempt_at=?,lease_owner='',lease_until=0,
                last_error=?,updated_at=?
            WHERE id=? AND status='processing' AND lease_owner=?""",
            Instant.now().getEpochSecond() + delay, code, now(), id, leaseOwner);
        if (changed == 1) retried.increment();
    }

    private static String errorCode(RuntimeException error) {
        if (error instanceof DeliveryException delivery) return delivery.code;
        if (error instanceof ResponseStatusException status) return "agent_unavailable_" + status.getStatusCode().value();
        return "agent_delivery_error";
    }

    private static final class DeliveryException extends RuntimeException {
        private final String code;
        private final boolean retryable;

        private DeliveryException(String code, boolean retryable) {
            super(code);
            this.code = code;
            this.retryable = retryable;
        }
    }
}
