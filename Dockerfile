# ---------------------------------------------------------------------------
# 出口代理容器 —— Dockerfile
#
# 适配平台: SnapDeploy / Railway / 其他 PaaS
# 关键约束: 平台注入 PORT 环境变量，容器必须监听 0.0.0.0:$PORT
# ---------------------------------------------------------------------------

FROM python:3.12-slim

WORKDIR /app

# 依赖分层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY proxy.py .

# CMD 用 shell 形式才能展开 $PORT 环境变量
# SnapDeploy 默认 PORT=3000，但实际值由运行时注入
CMD uvicorn proxy:app --host 0.0.0.0 --port ${PORT:-8080} --log-level info
