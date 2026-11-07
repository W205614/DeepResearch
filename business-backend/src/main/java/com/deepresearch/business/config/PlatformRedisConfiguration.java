package com.deepresearch.business.config;

import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

import io.lettuce.core.ClientOptions;
import io.lettuce.core.SocketOptions;
import io.lettuce.core.SslOptions;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceClientConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

@Configuration
public class PlatformRedisConfiguration {
    @Bean
    LettuceConnectionFactory platformRedisConnectionFactory(DeepResearchProperties properties) {
        URI uri = URI.create(properties.getRedisUrl());
        if (!java.util.Set.of("redis", "rediss").contains(uri.getScheme()) || uri.getHost() == null
                || uri.getQuery() != null || uri.getFragment() != null)
            throw new IllegalArgumentException("REDIS_URL must be a standalone redis/rediss URL without query or fragment");
        var configuration = new RedisStandaloneConfiguration(uri.getHost(), uri.getPort() < 0 ? 6379 : uri.getPort());
        String path = uri.getPath();
        configuration.setDatabase(path == null || path.isEmpty() || path.equals("/") ? 0 : Integer.parseInt(path.substring(1)));
        if (uri.getRawUserInfo() != null) {
            String[] credentials = uri.getRawUserInfo().split(":", 2);
            if (credentials.length != 2) throw new IllegalArgumentException("REDIS_URL credentials must contain a password");
            if (!credentials[0].isEmpty()) configuration.setUsername(decode(credentials[0]));
            configuration.setPassword(decode(credentials[1]));
        }
        var options = ClientOptions.builder().socketOptions(SocketOptions.builder()
            .connectTimeout(Duration.ofMillis(300)).build());
        if (!properties.getRedisSslCaCerts().isBlank()) {
            if (!uri.getScheme().equals("rediss")) throw new IllegalArgumentException("Redis CA requires rediss://");
            options.sslOptions(SslOptions.builder().trustManager(new java.io.File(properties.getRedisSslCaCerts())).build());
        }
        var client = LettuceClientConfiguration.builder().commandTimeout(Duration.ofMillis(300))
            .shutdownTimeout(Duration.ZERO).clientOptions(options.build());
        if (uri.getScheme().equals("rediss")) client.useSsl();
        return new LettuceConnectionFactory(configuration, client.build());
    }

    @Bean
    StringRedisTemplate platformRedisTemplate(LettuceConnectionFactory platformRedisConnectionFactory) {
        return new StringRedisTemplate(platformRedisConnectionFactory);
    }

    private static String decode(String value) {
        return URLDecoder.decode(value.replace("+", "%2B"), StandardCharsets.UTF_8);
    }
}
