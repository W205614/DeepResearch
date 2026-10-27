FROM node:22.16.0-alpine AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm config set maxsockets 1 \
 && npm config set fetch-retries 4 \
 && npm config set fetch-retry-mintimeout 1000 \
 && npm config set fetch-retry-maxtimeout 8000 \
 && for attempt in 1 2 3; do npm ci --no-audit --no-fund --prefer-offline && exit 0; sleep $((attempt * 3)); done; exit 1
COPY . .
RUN (sha256sum package.json package-lock.json index.html vite.config.ts tsconfig*.json nginx.conf; find src public -type f -print0 | sort -z | xargs -0 sha256sum) > /ui/source-fingerprint.txt
RUN npm run build
RUN find dist -type f -print0 | sort -z | xargs -0 sha256sum > /ui/dist-fingerprint.txt
FROM nginx:1.30.6-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951
RUN apk add --no-cache --upgrade 'brotli-libs>=1.2.1-r0' 'curl>=8.23.0-r0' 'libcurl>=8.23.0-r0'
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist /usr/share/nginx/html
COPY --from=build /ui/source-fingerprint.txt /etc/deepresearch/source-fingerprint.txt
COPY --from=build /ui/dist-fingerprint.txt /etc/deepresearch/dist-fingerprint.txt
EXPOSE 80
