FROM node:22.16.0-alpine AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm config set maxsockets 1 \
 && npm config set fetch-retries 4 \
 && npm config set fetch-retry-mintimeout 1000 \
 && npm config set fetch-retry-maxtimeout 8000 \
 && for attempt in 1 2 3; do npm ci --no-audit --no-fund --prefer-offline && exit 0; sleep $((attempt * 3)); done; exit 1
COPY . .
RUN npm run build
FROM nginx:1.28.0-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist /usr/share/nginx/html
EXPOSE 80
