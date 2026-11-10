package com.deepresearch.business.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.deepresearch.business.security.ApiRateLimitFilter;
import com.deepresearch.business.security.PlatformAccess;
import com.deepresearch.business.security.PlatformAccountFilter;
import com.deepresearch.business.security.SharedApiRateLimiter;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import jakarta.servlet.http.Cookie;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPublicKey;
import java.time.Instant;
import java.util.Date;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.mock.web.MockServletContext;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.context.support.AnnotationConfigWebApplicationContext;
import org.springframework.web.servlet.config.annotation.EnableWebMvc;

class SecurityConfigBearerTest {
    private AnnotationConfigWebApplicationContext context;
    private MockMvc mvc;
    private String token;
    private MutationEndpoint endpoint;

    @BeforeEach
    void actualSecurityChainWithLocallySignedJwt() throws Exception {
        context = new AnnotationConfigWebApplicationContext();
        context.setServletContext(new MockServletContext());
        context.register(IsolatedConfiguration.class);
        context.refresh();
        mvc = MockMvcBuilders.webAppContextSetup(context).apply(springSecurity()).build();
        endpoint = context.getBean(MutationEndpoint.class);
        SignedJWT signed = new SignedJWT(new JWSHeader(JWSAlgorithm.RS256), new JWTClaimsSet.Builder()
            .subject("isolated-user").issueTime(new Date())
            .expirationTime(Date.from(Instant.now().plusSeconds(120))).build());
        signed.sign(new RSASSASigner(context.getBean(KeyPair.class).getPrivate()));
        token = signed.serialize();
    }

    @AfterEach
    void closeIsolatedContext() {
        if (context != null) context.close();
    }

    @Test
    void browserCookiesAndSessionCannotAuthenticateACrossSiteMutation() throws Exception {
        var security = SecurityContextHolder.createEmptyContext();
        security.setAuthentication(new JwtAuthenticationToken(context.getBean(JwtDecoder.class).decode(token)));
        MockHttpSession session = new MockHttpSession();
        session.setAttribute(HttpSessionSecurityContextRepository.SPRING_SECURITY_CONTEXT_KEY, security);
        mvc.perform(post("/api/test-write").header("Origin", "https://other-origin.invalid")
                .session(session).cookie(new Cookie("dr-token", token), new Cookie("JSESSIONID", session.getId()))
                .contentType("application/x-www-form-urlencoded").param("access_token", token))
            .andExpect(status().isUnauthorized());
        assertThat(endpoint.writes).hasValue(0);
    }

    @Test
    void bearerQueryAndCookieAreNotAlternateCredentialSources() throws Exception {
        mvc.perform(post("/api/test-write?access_token=" + token))
            .andExpect(status().isUnauthorized());
        mvc.perform(post("/api/test-write").cookie(new Cookie("Authorization", "Bearer " + token)))
            .andExpect(status().isUnauthorized());
        assertThat(endpoint.writes).hasValue(0);
    }

    @Test
    void realVerifiedBearerHeaderAuthorizesWithoutCreatingACookieSession() throws Exception {
        var accepted = mvc.perform(post("/api/test-write").header("Authorization", "Bearer " + token))
            .andExpect(status().isOk()).andReturn();
        assertThat(endpoint.writes).hasValue(1);
        assertThat(accepted.getResponse().getHeaders("Set-Cookie")).isEmpty();
        assertThat(accepted.getRequest().getSession(false)).isNull();
        mvc.perform(post("/api/test-write")).andExpect(status().isUnauthorized());
        assertThat(endpoint.writes).hasValue(1);
    }

    @Test
    void invalidHeaderCannotBorrowAValidCookieCredential() throws Exception {
        mvc.perform(post("/api/test-write").header("Authorization", "Bearer invalid-isolated-token")
                .cookie(new Cookie("dr-token", token)))
            .andExpect(status().isUnauthorized());
        assertThat(endpoint.writes).hasValue(0);
    }

    @Configuration
    @EnableWebSecurity
    @EnableWebMvc
    @Import(SecurityConfig.class)
    static class IsolatedConfiguration {
        @Bean KeyPair keyPair() throws Exception {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
            generator.initialize(2048);
            return generator.generateKeyPair();
        }

        @Bean JwtDecoder decoder(KeyPair pair) {
            return NimbusJwtDecoder.withPublicKey((RSAPublicKey) pair.getPublic()).build();
        }

        @Bean PlatformAccess platform() { return mock(PlatformAccess.class); }

        @Bean PlatformAccountFilter accounts(PlatformAccess platform) {
            return new PlatformAccountFilter(platform);
        }

        @Bean ApiRateLimitFilter limits(PlatformAccess platform) {
            SharedApiRateLimiter shared = mock(SharedApiRateLimiter.class);
            when(shared.consume(anyString())).thenReturn(SharedApiRateLimiter.Result.ALLOWED);
            return new ApiRateLimitFilter(new DeepResearchProperties(), new SimpleMeterRegistry(), shared, platform);
        }

        @Bean MutationEndpoint mutationEndpoint() { return new MutationEndpoint(); }
    }

    @RestController
    static class MutationEndpoint {
        final AtomicInteger writes = new AtomicInteger();

        @PostMapping("/api/test-write")
        int write() { return writes.incrementAndGet(); }
    }
}
