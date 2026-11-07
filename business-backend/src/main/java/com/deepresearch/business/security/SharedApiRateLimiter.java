package com.deepresearch.business.security;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;

import com.deepresearch.business.config.DeepResearchProperties;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

@Component
public class SharedApiRateLimiter {
    public enum Result { ALLOWED, LIMITED, UNAVAILABLE }

    private static final DefaultRedisScript<Long> CONSUME = new DefaultRedisScript<>("""
        local stamp=redis.call('TIME')
        local now=tonumber(stamp[1])*1000+tonumber(stamp[2])/1000
        local function available(key,rate,capacity)
          local bucket=redis.call('HMGET',key,'tokens','stamp')
          local tokens=tonumber(bucket[1]) or capacity
          local last=tonumber(bucket[2]) or now
          return math.min(capacity,tokens+math.max(0,now-last)*rate/1000)
        end
        local subject=available(KEYS[1],tonumber(ARGV[1]),tonumber(ARGV[2]))
        local global=available(KEYS[2],tonumber(ARGV[3]),tonumber(ARGV[4]))
        if subject<1 or global<1 then return 0 end
        redis.call('HSET',KEYS[1],'tokens',subject-1,'stamp',now)
        redis.call('HSET',KEYS[2],'tokens',global-1,'stamp',now)
        redis.call('PEXPIRE',KEYS[1],600000)
        redis.call('PEXPIRE',KEYS[2],600000)
        return 1
        """, Long.class);
    private final StringRedisTemplate redis;
    private final DeepResearchProperties properties;
    private volatile boolean available = true;

    public SharedApiRateLimiter(StringRedisTemplate redis, DeepResearchProperties properties, MeterRegistry registry) {
        this.redis = redis;
        this.properties = properties;
        registry.gauge("deepresearch.business.redis.available", this, value -> value.available ? 1 : 0);
    }

    public Result consume(String subject) {
        String prefix = "dr:" + properties.getDeploymentId() + ":api-limit:";
        try {
            Long allowed = redis.execute(CONSUME, List.of(prefix + "subject:" + digest(subject), prefix + "global"),
                String.valueOf(properties.getApiRateLimitPerSecond()), String.valueOf(properties.getApiRateLimitBurst()),
                String.valueOf(properties.getApiGlobalRateLimitPerSecond()), String.valueOf(properties.getApiGlobalRateLimitBurst()));
            available = true;
            return Long.valueOf(1).equals(allowed) ? Result.ALLOWED : Result.LIMITED;
        } catch (DataAccessException failure) {
            available = false;
            return Result.UNAVAILABLE;
        }
    }

    public boolean available() { return available; }

    private static String digest(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
