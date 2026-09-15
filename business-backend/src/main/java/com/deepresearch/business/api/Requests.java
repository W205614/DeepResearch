package com.deepresearch.business.api;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public final class Requests {
    private Requests() {}

    public static final class Run {
        private String dataPolicy = "internal";
        @Size(max = 4000, message = "研究主题不能超过 4000 字")
        private String topic = "";
        @Size(max = 4, message = "最多上传 4 张图片")
        private List<String> attachmentIds = new ArrayList<>();
        private String threadId;
        private String mode = "auto";
        @NotBlank(message = "请求编号不能为空")
        @Size(min = 8, max = 80, message = "请求编号长度必须为 8 到 80")
        @Pattern(regexp = "^[a-zA-Z0-9_-]+$", message = "请求编号格式无效")
        private String clientRequestId;

        @AssertTrue(message = "请输入文字或上传图片")
        public boolean isInputPresent() { return topic != null && !topic.isBlank() || attachmentIds != null && !attachmentIds.isEmpty(); }
        @AssertTrue(message = "图片不能重复")
        public boolean isAttachmentsUnique() { return attachmentIds == null || new HashSet<>(attachmentIds).size() == attachmentIds.size(); }
        @AssertTrue(message = "资料策略无效")
        public boolean isDataPolicyValid() { return List.of("public", "internal", "restricted").contains(dataPolicy); }
        @AssertTrue(message = "研究模式无效")
        public boolean isModeValid() { return List.of("auto", "quick", "deep").contains(mode); }

        public String normalizedTopic() { return topic == null || topic.isBlank() ? "描述图片并提取关键信息" : topic.trim(); }
        public String getDataPolicy() { return dataPolicy; }
        public void setDataPolicy(String value) { this.dataPolicy = value; }
        public String getTopic() { return topic; }
        public void setTopic(String value) { this.topic = value; }
        public List<String> getAttachmentIds() { return attachmentIds == null ? List.of() : attachmentIds; }
        public void setAttachmentIds(List<String> value) { this.attachmentIds = value; }
        public String getThreadId() { return threadId; }
        public void setThreadId(String value) { this.threadId = value; }
        public String getMode() { return mode; }
        public void setMode(String value) { this.mode = value; }
        public String getClientRequestId() { return clientRequestId; }
        public void setClientRequestId(String value) { this.clientRequestId = value; }
    }

    public record Thread(@NotBlank @Size(max = 100) String title) {}
    public record Workspace(@NotBlank @Size(max = 100) String name) {}
    public record Membership(
        @NotBlank @Size(max = 128) @Pattern(regexp = "^[a-zA-Z0-9._:@-]+$") String subject,
        @NotBlank @Pattern(regexp = "admin|researcher|viewer") String role) {}
    public record WorkspaceLimit(@Min(1) @Max(20) int concurrentRunLimit) {}
    public record Memory(@NotBlank @Size(max = 1000) String content) {}
}
