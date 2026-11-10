FROM alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS busybox-fix
COPY . /recipe/
RUN sh /recipe/build.sh
RUN --network=none python3 /recipe/regression.py > /out/metadata/regression.json
