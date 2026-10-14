FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
ARG NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
ARG NEXT_PUBLIC_DEMO_SESSIONS=false
ARG MATCHLENS_API_ORIGIN
ENV NEXT_PUBLIC_DEMO_SESSIONS=$NEXT_PUBLIC_DEMO_SESSIONS
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL NEXT_TELEMETRY_DISABLED=1
ENV MATCHLENS_API_ORIGIN=$MATCHLENS_API_ORIGIN
RUN npm run build

FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S -g 10001 matchlens && adduser -S -u 10001 -G matchlens matchlens
COPY --from=build --chown=matchlens:matchlens /app/.next/standalone ./
COPY --from=build --chown=matchlens:matchlens /app/.next/static ./.next/static
COPY --from=build --chown=matchlens:matchlens /app/public ./public
COPY THIRD_PARTY_NOTICES.txt ./THIRD_PARTY_NOTICES.txt
USER matchlens
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s CMD node -e "fetch('http://127.0.0.1:3000/demo').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node","server.js"]
