package com.deepresearch.business;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;

import com.deepresearch.business.config.DeepResearchProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class DeepResearchPropertiesTest {
    @TempDir
    java.nio.file.Path temp;

    @Test
    void fileTokenOverridesEnvironmentValue() throws Exception {
        java.nio.file.Path token = temp.resolve("service-token");
        Files.writeString(token, "file-secret\n");
        DeepResearchProperties properties = new DeepResearchProperties();
        properties.setInternalToken("environment-secret");
        properties.setInternalTokenFile(token.toString());
        assertThat(properties.resolvedInternalToken()).isEqualTo("file-secret");
    }
}
