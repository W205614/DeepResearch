package com.deepresearch.business.security;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.server.ResponseStatusException;

@Component
public class PlatformAccountFilter extends OncePerRequestFilter {
    private final PlatformAccess platform;

    public PlatformAccountFilter(PlatformAccess platform) { this.platform = platform; }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith("/api/") || request.getRequestURI().equals("/api/auth/config");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        var authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication instanceof JwtAuthenticationToken token && authentication.isAuthenticated()) {
            try { platform.authenticate(token.getToken()); }
            catch (ResponseStatusException rejected) {
                response.setStatus(rejected.getStatusCode().value());
                response.setCharacterEncoding(StandardCharsets.UTF_8.name());
                response.setContentType("application/json");
                response.getWriter().write(rejected.getStatusCode().value() == 401
                    ? "{\"code\":\"session_revoked\",\"detail\":\"登录会话已失效，请重新登录\"}"
                    : "{\"code\":\"account_disabled\",\"detail\":\"账号已停用，请联系平台管理员\"}");
                return;
            } catch (org.springframework.dao.DataAccessException unavailable) {
                response.setStatus(503);
                response.setCharacterEncoding(StandardCharsets.UTF_8.name());
                response.setContentType("application/json");
                response.getWriter().write("{\"code\":\"database_unavailable\",\"detail\":\"账号状态暂时无法核实，请稍后重试\"}");
                return;
            }
        }
        chain.doFilter(request, response);
    }
}
