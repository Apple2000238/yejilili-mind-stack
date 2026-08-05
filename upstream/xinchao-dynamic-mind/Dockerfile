# Node 20 alpine — digest 需在构建前通过以下命令更新：
#   docker pull node:20-alpine && docker inspect --format='{{index .RepoDigests 0}}' node:20-alpine
FROM node:20-alpine@sha256:PLACEHOLDER_UPDATE_BEFORE_BUILD

WORKDIR /app
COPY package.json ./
COPY src ./src
COPY configs ./configs
RUN mkdir -p /app/state && chown -R node:node /app

USER node
ENV NODE_ENV=production
EXPOSE 18110
CMD ["node", "src/server.js"]
