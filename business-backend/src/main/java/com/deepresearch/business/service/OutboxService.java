package com.deepresearch.business.service;

import static com.deepresearch.business.security.WorkspaceAccess.now;
import static com.deepresearch.business.security.WorkspaceAccess.uid;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class OutboxService {
    private final JdbcTemplate jdbc;
    private final AgentClient agent;

    public OutboxService(JdbcTemplate jdbc, AgentClient agent) {
        this.jdbc = jdbc;
        this.agent = agent;
    }

    public void enqueue(String runId, String eventType, String payload) {
        jdbc.update("""
            INSERT INTO business_outbox(id,aggregate_id,event_type,payload,status,attempts,next_attempt_at,created_at,updated_at)
            SELECT ?,?,?,?,'pending',0,0,?,? WHERE NOT EXISTS(
                SELECT 1 FROM business_outbox WHERE aggregate_id=? AND event_type=? AND status='pending')""",
            uid(), runId, eventType, payload, now(), now(), runId, eventType);
    }

    @Scheduled(fixedDelay = 500)
    public void dispatch() {
        List<Map<String, Object>> rows;
        try {
            rows = jdbc.queryForList("""
                SELECT * FROM business_outbox WHERE status='pending' AND next_attempt_at<=?
                ORDER BY created_at LIMIT 20""", Instant.now().getEpochSecond());
        } catch (Exception migrationNotReady) {
            return;
        }
        for (Map<String, Object> row : rows) deliver(row);
    }

    private void deliver(Map<String, Object> row) {
        String id = String.valueOf(row.get("id"));
        String runId = String.valueOf(row.get("aggregate_id"));
        String type = String.valueOf(row.get("event_type"));
        String payload = String.valueOf(row.get("payload"));
        try {
            AgentClient.AgentResponse response = switch (type) {
                case "research.schedule", "research.resume" -> agent.internal("/internal/agent/runs/" + runId + "/schedule", payload);
                case "research.abort" -> agent.internal("/internal/agent/runs/" + runId + "/abort", "{}");
                default -> throw new IllegalStateException("unknown outbox event " + type);
            };
            if (!response.successful()) throw new IllegalStateException("agent returned " + response.status());
            jdbc.update("UPDATE business_outbox SET status='delivered',updated_at=? WHERE id=? AND status='pending'", now(), id);
        } catch (RuntimeException error) {
            int attempts = ((Number) row.get("attempts")).intValue() + 1;
            long delay = Math.min(30, 1L << Math.min(5, attempts));
            jdbc.update("""
                UPDATE business_outbox SET attempts=?,next_attempt_at=?,updated_at=?
                WHERE id=? AND status='pending'""", attempts, Instant.now().getEpochSecond() + delay, now(), id);
        }
    }
}
