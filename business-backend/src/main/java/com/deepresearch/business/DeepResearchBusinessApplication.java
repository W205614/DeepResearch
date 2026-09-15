package com.deepresearch.business;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@EnableScheduling
@SpringBootApplication
public class DeepResearchBusinessApplication {
    public static void main(String[] args) {
        SpringApplication.run(DeepResearchBusinessApplication.class, args);
    }
}
