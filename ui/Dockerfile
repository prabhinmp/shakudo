# ---- build stage: compile the React app ----
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build

# ---- runtime stage: unprivileged nginx (uid 101, no root). nginx.conf is supplied by the Helm chart ConfigMap ----
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html/ui
EXPOSE 8080
