package com.deepresearch.business.service;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;

import com.deepresearch.business.config.DeepResearchProperties;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
public class AgentClient {
    private static final List<String> FORWARDED_REQUEST_HEADERS = List.of(
        "Authorization", "X-Workspace-ID", "Last-Event-ID", "X-Confirm-Delete", "Content-Type", "Accept");
    private static final List<String> FORWARDED_RESPONSE_HEADERS = List.of(
        "content-type", "content-disposition", "cache-control");

    private final HttpClient client = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(5))
        .build();
    private final DeepResearchProperties properties;

    public AgentClient(DeepResearchProperties properties) {
        this.properties = properties;
    }

    public AgentResponse forward(HttpServletRequest source, byte[] body) {
        HttpRequest.Builder request = HttpRequest.newBuilder(uri(source.getRequestURI(), source.getQueryString()))
            .timeout(Duration.ofSeconds(300));
        FORWARDED_REQUEST_HEADERS.forEach(name -> {
            String value = source.getHeader(name);
            if (value != null && !value.isBlank()) request.header(name, value);
        });
        request.method(source.getMethod(), body.length == 0
            ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofByteArray(body));
        return send(request.build());
    }

    public AgentResponse get(String path) {
        return send(HttpRequest.newBuilder(uri(path, null)).GET().timeout(Duration.ofSeconds(5)).build());
    }

    public AgentResponse internal(String path, String json) {
        String token = properties.resolvedInternalToken();
        if (token.isBlank()) throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "内部服务令牌未配置");
        HttpRequest request = HttpRequest.newBuilder(uri(path, null))
            .timeout(Duration.ofSeconds(10))
            .header("Authorization", "Bearer " + token)
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .build();
        return send(request);
    }

    private AgentResponse send(HttpRequest request) {
        try {
            HttpResponse<byte[]> response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
            HttpHeaders headers = new HttpHeaders();
            for (String name : FORWARDED_RESPONSE_HEADERS) {
                response.headers().firstValue(name).ifPresent(value -> headers.add(name, value));
            }
            return new AgentResponse(response.statusCode(), headers, response.body());
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Agent 服务调用被中断", interrupted);
        } catch (IOException error) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Agent 服务暂不可用", error);
        }
    }

    private URI uri(String path, String query) {
        String base = properties.getAgentBaseUrl().replaceAll("/+$", "");
        return URI.create(base + path + (query == null || query.isBlank() ? "" : "?" + query));
    }

    public record AgentResponse(int status, HttpHeaders headers, byte[] body) {
        public boolean successful() { return status >= 200 && status < 300; }
        public Map<String, List<String>> headerMap() { return headers; }
    }
}
