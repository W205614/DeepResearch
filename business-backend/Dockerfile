FROM maven:3.9.11-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY pom.xml .
COPY src src
RUN (sha256sum pom.xml; find src/main -type f -print0 | sort -z | xargs -0 sha256sum) > /workspace/source-fingerprint.txt
RUN --mount=type=cache,target=/root/.m2 \
    for attempt in 1 2 3; do \
      mvn -B -q -DskipTests -Dmaven.wagon.http.retryHandler.count=5 package && exit 0; \
      find /root/.m2 -name '*.lastUpdated' -delete; \
      sleep $((attempt * 3)); \
    done; exit 1

FROM alpine:3.24.3 AS jre-security-update
RUN for attempt in 1 2 3; do \
      apk add --no-cache ca-certificates curl gnupg tar libstdc++ zlib && exit 0; \
      sleep "$attempt"; \
    done; exit 1
COPY runtime/adoptium-public.asc /tmp/adoptium-public.asc
RUN set -eu; \
    echo 'a46d5d3ab75c3c86dddf1bfd2957a067a24b1c6b2d2ed2bc69294bf970c5160b  /tmp/adoptium-public.asc' | sha256sum -c -; \
    gpg --batch --import /tmp/adoptium-public.asc; \
    gpg --batch --with-colons --fingerprint | grep -F ':3B04D753C9050D9A5D343F39843C48A565F8F04B:'; \
    base='https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9'; \
    curl --fail --location --retry 3 --connect-timeout 15 --max-time 240 "$base/OpenJDK21U-jre_x64_alpine-linux_hotspot_21.0.13_9.tar.gz" -o /tmp/jre.tar.gz; \
    curl --fail --location --retry 3 --connect-timeout 15 --max-time 90 "$base/OpenJDK21U-jre_x64_alpine-linux_hotspot_21.0.13_9.tar.gz.sig" -o /tmp/jre.tar.gz.sig; \
    echo '9138eab0f7d230e17163953f015ec9ae2431dbc49069c658b7f991e45b54fb9b  /tmp/jre.tar.gz' | sha256sum -c -; \
    gpg --batch --verify /tmp/jre.tar.gz.sig /tmp/jre.tar.gz; \
    curl --fail --location --retry 3 --connect-timeout 15 --max-time 90 "$base/OpenJDK21U-sbom_x64_alpine-linux_hotspot_21.0.13_9.json" -o /tmp/temurin-sbom.json; \
    curl --fail --location --retry 3 --connect-timeout 15 --max-time 90 "$base/OpenJDK21U-sbom_x64_alpine-linux_hotspot_21.0.13_9.json.sig" -o /tmp/temurin-sbom.json.sig; \
    echo 'daf3c6b803ecc5286d3c4eadc913ab8ebdd72c22f575aed364704a4cdc11bcb4  /tmp/temurin-sbom.json' | sha256sum -c -; \
    gpg --batch --verify /tmp/temurin-sbom.json.sig /tmp/temurin-sbom.json; \
    mkdir -p /opt/verified-java; \
    tar -xzf /tmp/jre.tar.gz -C /opt/verified-java --strip-components=1; \
    /opt/verified-java/bin/java --version

FROM eclipse-temurin:21-jre-alpine@sha256:b48e02e0ea88d7bfb305335df94923bdef394905f4147dccd52e873cf17b1088
# Refresh the affected OS library even when the upstream JRE image still ships it.
RUN apk add --no-cache --upgrade 'libexpat>=2.9.0-r0' 'brotli-libs>=1.2.1-r0' \
    && apk del --no-network gnupg coreutils \
    && rm -rf /opt/java/openjdk \
    && addgroup -S app && adduser -S -G app app
COPY --from=jre-security-update /opt/verified-java /opt/java/openjdk
COPY --from=jre-security-update /tmp/temurin-sbom.json /opt/java/temurin-sbom.json
WORKDIR /app
COPY --from=build /workspace/target/business-backend-0.1.0.jar app.jar
COPY --from=build /workspace/source-fingerprint.txt /app/source-fingerprint.txt
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/app.jar"]
