package com.deepresearch.business.security;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class ApiRateLimitFilterTest {
    @Test
    void tokenBucketRejectsBurstAndRefillsWithoutSleeping() {
        long now = 10_000_000_000L;
        ApiRateLimitFilter.Bucket bucket = new ApiRateLimitFilter.Bucket(2, 2, now);
        assertThat(bucket.tryConsume(now)).isTrue();
        assertThat(bucket.tryConsume(now)).isTrue();
        assertThat(bucket.tryConsume(now)).isFalse();
        assertThat(bucket.tryConsume(now + 500_000_000L)).isTrue();
        assertThat(bucket.tryConsume(now + 500_000_000L)).isFalse();
    }
}
