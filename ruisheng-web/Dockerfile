# ---- Build stage ----
FROM node:20-alpine AS builder

RUN npm install -g pnpm@9

WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

ENV VITE_API_BASE=/api
ENV VITE_WS_BASE=/ws

RUN pnpm build

# ---- Serve stage ----
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
