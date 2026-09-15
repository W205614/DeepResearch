package com.deepresearch.business.config;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "deepresearch")
public class DeepResearchProperties {
    private String authMode = "oidc";
    private String oidcIssuer = "";
    private String agentBaseUrl = "http://localhost:8000";
    private String internalToken = "";
    private String internalTokenFile = "";
    private boolean allowInternalModelProcessing;
    private int workspaceConcurrentRunLimit = 2;
    private int maxQueuedRuns = 20;
    private long maxRunTotalSeconds = 1200;
    private int maxRunCallAttempts = 80;
    private long maxRunReservedTokens = 2_000_000;

    public String resolvedInternalToken() {
        if (internalTokenFile != null && !internalTokenFile.isBlank()) {
            try {
                return Files.readString(Path.of(internalTokenFile)).trim();
            } catch (IOException ignored) {
                return "";
            }
        }
        return internalToken == null ? "" : internalToken.trim();
    }

    public String getAuthMode() { return authMode; }
    public void setAuthMode(String authMode) { this.authMode = authMode; }
    public String getOidcIssuer() { return oidcIssuer; }
    public void setOidcIssuer(String oidcIssuer) { this.oidcIssuer = oidcIssuer; }
    public String getAgentBaseUrl() { return agentBaseUrl; }
    public void setAgentBaseUrl(String agentBaseUrl) { this.agentBaseUrl = agentBaseUrl; }
    public String getInternalToken() { return internalToken; }
    public void setInternalToken(String internalToken) { this.internalToken = internalToken; }
    public String getInternalTokenFile() { return internalTokenFile; }
    public void setInternalTokenFile(String internalTokenFile) { this.internalTokenFile = internalTokenFile; }
    public boolean isAllowInternalModelProcessing() { return allowInternalModelProcessing; }
    public void setAllowInternalModelProcessing(boolean value) { this.allowInternalModelProcessing = value; }
    public int getWorkspaceConcurrentRunLimit() { return workspaceConcurrentRunLimit; }
    public void setWorkspaceConcurrentRunLimit(int value) { this.workspaceConcurrentRunLimit = value; }
    public int getMaxQueuedRuns() { return maxQueuedRuns; }
    public void setMaxQueuedRuns(int value) { this.maxQueuedRuns = value; }
    public long getMaxRunTotalSeconds() { return maxRunTotalSeconds; }
    public void setMaxRunTotalSeconds(long value) { this.maxRunTotalSeconds = value; }
    public int getMaxRunCallAttempts() { return maxRunCallAttempts; }
    public void setMaxRunCallAttempts(int value) { this.maxRunCallAttempts = value; }
    public long getMaxRunReservedTokens() { return maxRunReservedTokens; }
    public void setMaxRunReservedTokens(long value) { this.maxRunReservedTokens = value; }
}
