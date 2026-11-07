package com.deepresearch.business.api;

import static org.mockito.Mockito.mock;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.deepresearch.business.config.DeepResearchProperties;
import com.deepresearch.business.service.AgentClient;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class HealthControllerTest {
    @Test
    void exposesRuntimeOidcClientAndScopesWithoutServiceCredentials() throws Exception {
        DeepResearchProperties properties = new DeepResearchProperties();
        properties.setOidcIssuer("https://identity.company.test/tenant");
        properties.setOidcClientId("corporate-web");
        properties.setOidcScopes("openid profile offline_access api.read");
        properties.setOidcRegistrationEnabled(false);
        properties.setInternalToken("private-service-secret");
        var mvc = MockMvcBuilders.standaloneSetup(new HealthController(
                mock(JdbcTemplate.class), mock(AgentClient.class), properties)).build();

        mvc.perform(get("/api/auth/config"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.issuer").value("https://identity.company.test/tenant"))
                .andExpect(jsonPath("$.client_id").value("corporate-web"))
                .andExpect(jsonPath("$.scopes").value("openid profile offline_access api.read"))
                .andExpect(jsonPath("$.registration_enabled").value(false))
                .andExpect(jsonPath("$.internalToken").doesNotExist())
                .andExpect(jsonPath("$.length()").value(5));
    }
}
