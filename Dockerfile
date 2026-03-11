# syntax=docker/dockerfile:1
FROM node:20-alpine AS base
WORKDIR /app

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
