# ---- 前端构建阶段（React + Vite，产物 dist/ 拷入运行镜像） ----
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY frontend/ ./
RUN npm run build

# ---- 运行阶段（Python 3.12 + ffmpeg） ----
FROM python:3.12-alpine3.23
WORKDIR /app
RUN apk add --no-cache ffmpeg
COPY requirements.txt requirements.txt
RUN pip3 config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip3 install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist
RUN chmod +x /app/entrypoint.sh
ENV TZ=Asia/Shanghai
EXPOSE 5000
CMD ["/app/entrypoint.sh"]
