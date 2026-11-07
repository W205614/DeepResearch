package com.deepresearch.business.security;

import java.time.Instant;

public record WorkspaceIdentity(String workspaceId, String subject, String role, String displayName, Instant issuedAt) {
    public WorkspaceIdentity(String workspaceId, String subject, String role, String displayName) {
        this(workspaceId, subject, role, displayName, null);
    }
    public boolean hasRole(String... allowed) {
        for (String value : allowed) {
            if (value.equals(role)) return true;
        }
        return false;
    }
}
