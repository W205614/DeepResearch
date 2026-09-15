package com.deepresearch.business.api;

import java.util.Map;

import jakarta.validation.ConstraintViolationException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(ResponseStatusException.class)
    ResponseEntity<Map<String, String>> status(ResponseStatusException error) {
        return ResponseEntity.status(error.getStatusCode()).body(Map.of("detail", error.getReason() == null ? "请求失败" : error.getReason()));
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, ConstraintViolationException.class,
        HttpMessageNotReadableException.class, IllegalArgumentException.class})
    ResponseEntity<Map<String, String>> invalid(Exception error) {
        String detail = error instanceof MethodArgumentNotValidException invalid
            ? invalid.getBindingResult().getAllErrors().stream().findFirst().map(item -> item.getDefaultMessage()).orElse("请求参数无效")
            : error.getMessage();
        return ResponseEntity.unprocessableEntity().body(Map.of("detail", detail == null ? "请求参数无效" : detail));
    }

    @ExceptionHandler(DataIntegrityViolationException.class)
    ResponseEntity<Map<String, String>> conflict(DataIntegrityViolationException error) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("detail", "请求与当前数据状态冲突"));
    }
}
