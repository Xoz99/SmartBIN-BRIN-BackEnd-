# syntax=docker/dockerfile:1
FROM node:20-alpine AS base
WORKDIR /app

# OpenSSL + libc compat — WAJIB buat Prisma di Alpine (kalau tidak: "failed to
# detect libssl/openssl" → gagal konek PostgreSQL). Dipasang SEBELUM prisma generate
# supaya engine yang di-download cocok (linux-musl-openssl-3.0.x).
RUN apk add --no-cache openssl libc6-compat

# Install dependencies
COPY package*.json ./
RUN npm ci --omit=dev

# Generate Prisma client
COPY prisma ./prisma
RUN npx prisma generate

# Copy source
COPY src ./src
COPY server.js ./

# Create logs directory
RUN mkdir -p logs

EXPOSE 3000

CMD ["node", "server.js"]
