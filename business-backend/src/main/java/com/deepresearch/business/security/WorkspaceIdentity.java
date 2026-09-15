package com.deepresearch.business.security;

public record WorkspaceIdentity(String workspaceId, String subject, String role, String displayName) {
    public boolean hasRole(String... allowed) {
        for (String value : allowed) {
            if (value.equals(role)) return true;
        }
        return false;
    }
}
