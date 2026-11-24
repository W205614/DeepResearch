package com.deepresearch.business.api;

import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

/** Encode in bounded chunks; avoid allocating a second report-sized UTF-8 byte array. */
final class MarkdownDownload {
    private MarkdownDownload() {}

    static StreamingResponseBody stream(String markdown, Runnable requireCurrent) {
        return output -> {
            OutputStreamWriter writer = new OutputStreamWriter(output, StandardCharsets.UTF_8);
            writeChunks(writer, markdown, requireCurrent);
            writer.flush();
        };
    }

    static void writeChunks(Writer writer, String markdown, Runnable requireCurrent) throws IOException {
        for (int offset = 0; offset < markdown.length(); offset += 4096) {
            requireCurrent.run();
            writer.write(markdown, offset, Math.min(4096, markdown.length() - offset));
        }
    }

    static String draftQuality(Map<?, ?> validation) {
        String quality = String.valueOf(validation.get("quality"));
        return switch (quality) {
            case "complete" -> Boolean.FALSE.equals(validation.get("original_request_answered"))
                || (validation.get("unanswered_questions") instanceof List<?> questions && !questions.isEmpty())
                ? "历史结果：完整性标记与未答信息不一致，需重新核验" : "完整研究结果（模型核验）";
            case "partial" -> "部分结果";
            case "insufficient" -> "证据不足";
            case "unverified" -> "未经研究证据核验";
            default -> "历史结果：核验信息不完整";
        };
    }
}
