package com.deepresearch.business.security;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.config.DeepResearchProperties;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

@Component
public class PlatformAccess {
    private final JdbcTemplate jdbc;
    private final DeepResearchProperties properties;

    public PlatformAccess(JdbcTemplate jdbc, DeepResearchProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    public void authenticate(Jwt jwt) {
        if (jwt.getIssuedAt() == null && "oidc".equals(properties.getAuthMode()))
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "登录凭证缺少签发时间，请重新登录");
        String subject = jwt.getSubject();
        if (subject == null || subject.isBlank() || subject.length() > 128)
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "登录凭证缺少有效主体");
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT status,revoked_before FROM platform_users WHERE subject=?", subject);
        if (rows.isEmpty()) {
            String display = jwt.getClaimAsString("preferred_username");
            if (display == null || display.isBlank()) display = subject;
            String stamp = Instant.now().toString();
            jdbc.update("""
                INSERT INTO platform_users(subject,status,revoked_before,display_name,created_at,updated_at)
                VALUES(?,'active',0,?,?,?) ON CONFLICT(subject) DO NOTHING""",
                subject, display.substring(0, Math.min(display.length(), 100)), stamp, stamp);
        }
        requireCurrent(jwt.getSubject(), jwt.getIssuedAt());
    }

    public void requireCurrent(String subject, Instant issuedAt) {
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT status,revoked_before FROM platform_users WHERE subject=?", subject);
        if (rows.isEmpty()) return; // Existing internal identities are backfilled by the additive migration.
        Map<String, Object> user = rows.getFirst();
        if (!"active".equals(user.get("status")))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "账号已停用，请联系平台管理员");
        long revoked = ((Number) user.get("revoked_before")).longValue();
        if (revoked > 0 && (issuedAt == null || issuedAt.getEpochSecond() <= revoked))
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "此前登录会话已撤销，请重新登录");
    }

    public void requireActive(String subject) {
        List<String> states = jdbc.queryForList("SELECT status FROM platform_users WHERE subject=?", String.class, subject);
        if (!states.isEmpty() && !"active".equals(states.getFirst()))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "账号已停用，请联系平台管理员");
    }

    public void lockCurrent(String subject, Instant issuedAt) {
        // Keep offboarding behind any mutation already authorized in this transaction.
        jdbc.queryForList("SELECT subject FROM platform_users WHERE subject=? FOR SHARE", subject);
        requireCurrent(subject, issuedAt);
    }

    public void requireOperator(Jwt jwt) {
        authenticate(jwt);
        if (!properties.operatorSubjects().contains(jwt.getSubject()))
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "仅平台运维管理员可执行此操作");
    }

    public boolean operator(String subject) { return properties.operatorSubjects().contains(subject); }

    public boolean migrationBarrierActive() {
        return !jdbc.queryForList("SELECT value FROM metadata WHERE key='embedding_migration_barrier' AND value<>''").isEmpty();
    }

    public void lockGovernance() {
        jdbc.query("SELECT pg_advisory_xact_lock(?)", result -> null, lockId("platform-governance"));
    }

    public static long lockId(String key) {
        try {
            return ByteBuffer.wrap(MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8))).getLong();
        } catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }
}
