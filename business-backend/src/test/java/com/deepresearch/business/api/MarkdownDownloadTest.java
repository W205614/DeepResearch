package com.deepresearch.business.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class MarkdownDownloadTest {
    @Test
    void downloadChecksCurrentAuthorizationBetweenBoundedChunks() throws Exception {
        String content = "研究资料\n".repeat(3000);
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        AtomicInteger checks = new AtomicInteger();
        MarkdownDownload.stream(content, checks::incrementAndGet).writeTo(output);
        assertThat(output.toString(StandardCharsets.UTF_8)).isEqualTo(content);
        assertThat(checks.get()).isGreaterThan(1);

        output.reset();
        checks.set(0);
        assertThatThrownBy(() -> MarkdownDownload.stream(content, () -> {
            if (checks.incrementAndGet() == 2) throw new IllegalStateException("account_disabled");
        }).writeTo(output)).isInstanceOf(IllegalStateException.class).hasMessage("account_disabled");
        assertThat(checks.get()).isEqualTo(2);
        assertThat(output.size()).isLessThan(content.getBytes(StandardCharsets.UTF_8).length);
    }
}
