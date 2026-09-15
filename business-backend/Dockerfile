FROM maven:3.9.11-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY pom.xml .
COPY src src
RUN --mount=type=cache,target=/root/.m2 \
    for attempt in 1 2 3; do \
      mvn -B -q -DskipTests -Dmaven.wagon.http.retryHandler.count=5 package && exit 0; \
      find /root/.m2 -name '*.lastUpdated' -delete; \
      sleep $((attempt * 3)); \
    done; exit 1

FROM eclipse-temurin:21-jre-alpine
RUN addgroup -S app && adduser -S -G app app
WORKDIR /app
COPY --from=build /workspace/target/business-backend-0.1.0.jar app.jar
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/app.jar"]
