FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_AGUI_AGENT_URL=http://localhost:8001/agent
ENV NEXT_PUBLIC_AGUI_AGENT_URL=$NEXT_PUBLIC_AGUI_AGENT_URL
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_PUBLIC_AGUI_AGENT_URL=http://localhost:8001/agent
COPY --from=builder /app ./
EXPOSE 3000
CMD ["npm", "run", "start", "--", "-p", "3000"]
