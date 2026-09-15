package com.deepresearch.business;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import com.deepresearch.business.api.Requests;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

class RunRequestValidationTest {
    private static Validator validator;

    @BeforeAll
    static void setUp() {
        validator = Validation.buildDefaultValidatorFactory().getValidator();
    }

    @Test
    void acceptsTextResearchWithStableClientRequestId() {
        Requests.Run request = new Requests.Run();
        request.setTopic("研究 Java 与 Python 服务边界");
        request.setClientRequestId("request-0001");
        request.setDataPolicy("public");
        assertThat(validator.validate(request)).isEmpty();
        assertThat(request.normalizedTopic()).isEqualTo("研究 Java 与 Python 服务边界");
    }

    @Test
    void rejectsDuplicateAttachmentsAndMissingInput() {
        Requests.Run request = new Requests.Run();
        request.setClientRequestId("request-0002");
        request.setAttachmentIds(List.of("same", "same"));
        assertThat(validator.validate(request)).anyMatch(item -> item.getMessage().equals("图片不能重复"));

        request.setAttachmentIds(List.of());
        assertThat(validator.validate(request)).anyMatch(item -> item.getMessage().equals("请输入文字或上传图片"));
    }

    @Test
    void imageOnlyResearchGetsExplicitTopic() {
        Requests.Run request = new Requests.Run();
        request.setClientRequestId("request-0003");
        request.setAttachmentIds(List.of("image-1"));
        assertThat(validator.validate(request)).isEmpty();
        assertThat(request.normalizedTopic()).isEqualTo("描述图片并提取关键信息");
    }
}
