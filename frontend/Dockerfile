ARG BASE_IMAGE=node:20-alpine
FROM ${BASE_IMAGE} AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM ${BASE_IMAGE}
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

# Next standalone binds HOSTNAME; default it to all interfaces so container health checks work.
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

EXPOSE 3000
CMD ["node", "server.js"]
