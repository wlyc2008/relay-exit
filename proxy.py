"""
出口代理容器 —— 通用 HTTP 代理出口

适配平台: SnapDeploy (免费层) / Railway / 其他 Docker PaaS

数据契约（与 GAS 完全一致）：
    输入: GET /proxy?url=https://example.com
    输出: {"status": 200, "contentType": "text/html; charset=utf-8", "b64": "Base64响应体"}

平台约束:
    - SnapDeploy 自动注入 PORT 环境变量，容器必须监听 0.0.0.0:$PORT
    - 健康检查: GET / 必须返回 200
"""

import base64
import logging
import os
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("exit-proxy")

# ---------- 应用 ----------

app = FastAPI(title="Relay Exit Proxy", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 常量 ----------

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 55.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # 8MB

# ---------- 响应模型 ----------


class ProxyResponse(BaseModel):
    status: int = Field(..., description="HTTP 状态码")
    contentType: str = Field(default="", description="Content-Type 原始值")
    b64: str = Field(default="", description="响应体的 Base64 编码")


class ErrorResponse(BaseModel):
    error: str
    status: int = 502


# ---------- 工具 ----------


def _truncate_b64(raw: bytes, max_bytes: int) -> str:
    if len(raw) > max_bytes:
        log.warning("响应体 %d B 超过上限 %d B，截断", len(raw), max_bytes)
        raw = raw[:max_bytes]
    return base64.b64encode(raw).decode("ascii")


def _pick_content_type(headers: dict) -> str:
    ct = headers.get("content-type") or headers.get("Content-Type") or ""
    return ct.strip()


# ---------- 路由 ----------


@app.get("/", tags=["meta"])
async def root() -> dict:
    """健康检查（SnapDeploy 容器启动时会探测这个路径）。"""
    return {"service": "relay-exit", "version": "1.0.0", "status": "ok"}


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict:
    return {"ok": True}


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """SnapDeploy / Railway 等 PaaS 健康检查路径。"""
    return {"status": "ok"}


@app.get("/actuator/health", tags=["meta"])
async def actuator_health() -> dict:
    """部分 PaaS 会用这个路径做健康检查。"""
    return {"status": "UP"}


@app.get("/proxy", response_model=ProxyResponse, tags=["proxy"])
async def proxy_url(
    url: str = Query(..., description="目标 URL，需要 http:// 或 https:// 前缀"),
    timeout: Optional[float] = Query(default=None, description="自定义读取超时（秒）"),
) -> ProxyResponse:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL 必须以 http:// 或 https:// 开头")

    read_timeout = timeout if timeout and 0 < timeout <= 120 else READ_TIMEOUT
    headers = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=read_timeout),
            follow_redirects=True,
            max_redirects=5,
            headers=headers,
        ) as client:
            log.info("抓取 %s", url)
            resp = await client.get(url)
    except httpx.ConnectTimeout:
        log.error("连接超时: %s", url)
        raise HTTPException(status_code=502, detail=f"连接超时: {url}")
    except httpx.ReadTimeout:
        log.error("读取超时: %s", url)
        raise HTTPException(status_code=504, detail=f"读取超时: {url}")
    except httpx.TooManyRedirects:
        log.error("重定向过多: %s", url)
        raise HTTPException(status_code=502, detail=f"重定向过多: {url}")
    except httpx.RequestError as exc:
        log.error("请求失败: %s -> %s", url, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    body = resp.content
    log.info(
        "完成  url=%s  status=%d  ct=%s  size=%d",
        url, resp.status_code, _pick_content_type(dict(resp.headers)), len(body),
    )

    return ProxyResponse(
        status=resp.status_code,
        contentType=_pick_content_type(dict(resp.headers)),
        b64=_truncate_b64(body, MAX_RESPONSE_BYTES),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=str(exc.detail), status=exc.status_code).model_dump(),
    )


# ---------- 启动入口 ----------

if __name__ == "__main__":
    import uvicorn

    # SnapDeploy / Railway 自动注入 PORT，本地开发默认 8080
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("proxy:app", host="0.0.0.0", port=port, log_level="info")
