package com.deepresearch.business.config;

import java.nio.charset.StandardCharsets;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpStatus;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
@EnableMethodSecurity
public class SecurityConfig {
    @Bean
    SecurityFilterChain apiSecurity(HttpSecurity http) throws Exception {
        return http
            .csrf(csrf -> csrf.disable())
            .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/healthz", "/livez", "/readyz", "/api/auth/config", "/metrics", "/health").permitAll()
                .requestMatchers("/api/**").authenticated()
                .anyRequest().denyAll())
            .oauth2ResourceServer(oauth -> oauth.jwt(Customizer.withDefaults())
                .authenticationEntryPoint((request, response, exception) -> {
                    response.setStatus(HttpStatus.UNAUTHORIZED.value());
                    response.setCharacterEncoding(StandardCharsets.UTF_8.name());
                    response.setContentType("application/json");
                    response.getWriter().write("{\"detail\":\"未提供有效登录凭证\"}");
                }))
            .exceptionHandling(errors -> errors.accessDeniedHandler((request, response, exception) -> {
                response.setStatus(HttpStatus.FORBIDDEN.value());
                response.setCharacterEncoding(StandardCharsets.UTF_8.name());
                response.setContentType("application/json");
                response.getWriter().write("{\"detail\":\"当前工作空间角色没有此权限\"}");
            }))
            .build();
    }
}
