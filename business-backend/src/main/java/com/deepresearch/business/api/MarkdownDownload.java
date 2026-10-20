package com.deepresearch.business.api;

import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

/** Encode in bounded chunks; avoid allocating a second report-sized UTF-8 byte array. */
final class MarkdownDownload {
    private MarkdownDownload() {}

    static StreamingResponseBody stream(String markdown) {
        return output -> {
            OutputStreamWriter writer = new OutputStreamWriter(output, StandardCharsets.UTF_8);
            for (int offset = 0; offset < markdown.length(); offset += 4096)
                writer.write(markdown, offset, Math.min(4096, markdown.length() - offset));
            writer.flush();
        };
    }
}
