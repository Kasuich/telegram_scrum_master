FROM node:22-alpine AS builder

WORKDIR /app

COPY apps/web-ui/package.json ./
RUN npm install

COPY apps/web-ui ./
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY apps/web-ui/nginx.conf /etc/nginx/conf.d/default.conf
