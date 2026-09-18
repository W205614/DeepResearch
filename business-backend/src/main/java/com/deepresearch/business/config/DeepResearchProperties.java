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
    private int outboxBatchSize = 20;
    private long outboxLeaseSeconds = 30;
    private int outboxMaxAttempts = 8;
    private long outboxBaseDelaySeconds = 2;
    private long outboxMaxDelaySeconds = 60;
    private int agentProxyMaxConcurrent = 8;
    private long agentProxyAcquireTimeoutMillis = 25;
    private long agentProxyTimeoutSeconds = 30;
    private int agentProxyCircuitFailureThreshold = 5;
    private long agentProxyCircuitOpenSeconds = 15;
    private int apiRateLimitPerSecond = 20;
    private int apiRateLimitBurst = 40;
    private int apiRateLimitMaxPrincipals = 10_000;
    private int apiGlobalRateLimitPerSecond = 250;
    private int apiGlobalRateLimitBurst = 250;
    private int apiMaxConcurrent = 32;
    private long apiConcurrencyAcquireTimeoutMillis = 10;

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
    public int getOutboxBatchSize() { return outboxBatchSize; }
    public void setOutboxBatchSize(int value) { this.outboxBatchSize = value; }
    public long getOutboxLeaseSeconds() { return outboxLeaseSeconds; }
    public void setOutboxLeaseSeconds(long value) { this.outboxLeaseSeconds = value; }
    public int getOutboxMaxAttempts() { return outboxMaxAttempts; }
    public void setOutboxMaxAttempts(int value) { this.outboxMaxAttempts = value; }
    public long getOutboxBaseDelaySeconds() { return outboxBaseDelaySeconds; }
    public void setOutboxBaseDelaySeconds(long value) { this.outboxBaseDelaySeconds = value; }
    public long getOutboxMaxDelaySeconds() { return outboxMaxDelaySeconds; }
    public void setOutboxMaxDelaySeconds(long value) { this.outboxMaxDelaySeconds = value; }
    public int getAgentProxyMaxConcurrent() { return agentProxyMaxConcurrent; }
    public void setAgentProxyMaxConcurrent(int value) { this.agentProxyMaxConcurrent = value; }
    public long getAgentProxyAcquireTimeoutMillis() { return agentProxyAcquireTimeoutMillis; }
    public void setAgentProxyAcquireTimeoutMillis(long value) { this.agentProxyAcquireTimeoutMillis = value; }
    public long getAgentProxyTimeoutSeconds() { return agentProxyTimeoutSeconds; }
    public void setAgentProxyTimeoutSeconds(long value) { this.agentProxyTimeoutSeconds = value; }
    public int getAgentProxyCircuitFailureThreshold() { return agentProxyCircuitFailureThreshold; }
    public void setAgentProxyCircuitFailureThreshold(int value) { this.agentProxyCircuitFailureThreshold = value; }
    public long getAgentProxyCircuitOpenSeconds() { return agentProxyCircuitOpenSeconds; }
    public void setAgentProxyCircuitOpenSeconds(long value) { this.agentProxyCircuitOpenSeconds = value; }
    public int getApiRateLimitPerSecond() { return apiRateLimitPerSecond; }
    public void setApiRateLimitPerSecond(int value) { this.apiRateLimitPerSecond = value; }
    public int getApiRateLimitBurst() { return apiRateLimitBurst; }
    public void setApiRateLimitBurst(int value) { this.apiRateLimitBurst = value; }
    public int getApiRateLimitMaxPrincipals() { return apiRateLimitMaxPrincipals; }
    public void setApiRateLimitMaxPrincipals(int value) { this.apiRateLimitMaxPrincipals = value; }
    public int getApiGlobalRateLimitPerSecond() { return apiGlobalRateLimitPerSecond; }
    public void setApiGlobalRateLimitPerSecond(int value) { this.apiGlobalRateLimitPerSecond = value; }
    public int getApiGlobalRateLimitBurst() { return apiGlobalRateLimitBurst; }
    public void setApiGlobalRateLimitBurst(int value) { this.apiGlobalRateLimitBurst = value; }
    public int getApiMaxConcurrent() { return apiMaxConcurrent; }
    public void setApiMaxConcurrent(int value) { this.apiMaxConcurrent = value; }
    public long getApiConcurrencyAcquireTimeoutMillis() { return apiConcurrencyAcquireTimeoutMillis; }
    public void setApiConcurrencyAcquireTimeoutMillis(long value) { this.apiConcurrencyAcquireTimeoutMillis = value; }
}
