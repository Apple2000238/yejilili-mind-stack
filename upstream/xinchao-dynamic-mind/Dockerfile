# Node 20 alpine — digest 需在构建前通过以下命令更新：
#   docker pull node:20-alpine && docker inspect --format='{{index .RepoDigests 0}}' node:20-alpine
FROM node:20-alpine@sha256:24a6c8ea6e5ded8f5884e3019b1562ba16f1d3e38d6a62f9a14e21d74bd90a91

WORKDIR /app
COPY package.json ./
COPY src ./src
COPY configs ./configs
RUN mkdir -p /app/state && chown -R node:node /app

USER node
ENV NODE_ENV=production
EXPOSE 18110
CMD ["node", "src/server.js"]
