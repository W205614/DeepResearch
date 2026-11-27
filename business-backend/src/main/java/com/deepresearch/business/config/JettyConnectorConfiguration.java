package com.deepresearch.business.config;

import java.util.Arrays;

import org.eclipse.jetty.server.ServerConnector;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.jetty.servlet.JettyServletWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
public class JettyConnectorConfiguration {
    @Bean
    WebServerFactoryCustomizer<JettyServletWebServerFactory> jettyAcceptQueue(
            @Value("${deepresearch.server.accept-count}") int acceptCount) {
        // Spring Boot exposes Jetty's connection limit separately from its TCP accept queue.
        return factory -> factory.addServerCustomizers(server -> Arrays.stream(server.getConnectors())
                .filter(ServerConnector.class::isInstance)
                .map(ServerConnector.class::cast)
                .forEach(connector -> connector.setAcceptQueueSize(acceptCount)));
    }
}
