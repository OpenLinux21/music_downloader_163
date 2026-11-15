#!/bin/sh
# 容器启动脚本：未挂载配置时生成默认 config.toml，再经统一入口 start.py 启动
if [ ! -f /app/config.toml ]; then
  cp /app/config.example.toml /app/config.toml
  # 容器内默认绑定 0.0.0.0 保证端口映射可达；如挂载了 config.toml 则以挂载内容为准
  sed -i 's/^host = "127.0.0.1"/host = "0.0.0.0"/' /app/config.toml
fi
exec python3 start.py
