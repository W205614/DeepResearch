package com.deepresearch.business.service;

import java.util.Map;
import java.util.Set;

/** Wire-only contract. Python owns graph recovery; Java never resets execution budgets. */
public final class ResearchCommandContract {
    public static final String VERSION = "research-command-v1";
    private ResearchCommandContract() { }

    public static Map<String, Object> schedule(boolean resume, int expectedAttempt) {
        if (expectedAttempt < 0) throw new IllegalArgumentException("negative attempt");
        return Map.of("contract_version", VERSION, "resume", resume, "expected_attempt", expectedAttempt);
    }

    public static boolean valid(Map<String, Object> payload) {
        if (!Set.of("contract_version", "resume", "expected_attempt").containsAll(payload.keySet())) return false;
        if (!payload.containsKey("contract_version") && payload.containsKey("expected_attempt")) return false;
        if (payload.containsKey("contract_version") && !VERSION.equals(payload.get("contract_version"))) return false;
        if (payload.containsKey("resume") && !(payload.get("resume") instanceof Boolean)) return false;
        if (payload.containsKey("expected_attempt")) {
            Object attempt = payload.get("expected_attempt");
            if (!(attempt instanceof Integer || attempt instanceof Long)) return false;
            long number = ((Number) attempt).longValue();
            if (number < 0 || number > Integer.MAX_VALUE) return false;
        }
        return true;
    }

    public static boolean canResume(String status, Object retryable, long deadline, long epoch,
                                     long calls, long reserved, long maxCalls, long maxTokens) {
        return Set.of("failed", "interrupted", "cancelled").contains(status)
            && !Boolean.FALSE.equals(retryable) && (deadline <= 0 || deadline > epoch)
            && calls < maxCalls && reserved < maxTokens;
    }
}
