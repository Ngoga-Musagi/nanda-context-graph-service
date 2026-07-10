FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:alpine
# Railway (and most PaaS) inject $PORT at runtime; default to 80 for plain
# `docker run` / docker-compose. The nginx image entrypoint renders templates
# in /etc/nginx/templates via envsubst. Restrict substitution to ${PORT} so
# nginx's own $uri/$host are left intact.
ENV PORT=80
ENV NGINX_ENVSUBST_FILTER=^PORT$
COPY --from=build /app/dist /usr/share/nginx/html
COPY default.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80
