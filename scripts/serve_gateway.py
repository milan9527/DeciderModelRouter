"""Independent development frontend with a same-origin streaming API reverse proxy."""

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "gateway"
# Only end-to-end application headers cross this local proxy.
REQUEST_HEADERS = {
    "accept",
    "content-type",
    "cookie",
    "origin",
    "sec-fetch-site",
    "x-decider-client",
    "host",
}
RESPONSE_HEADERS = {
    "content-type",
    "cache-control",
    "set-cookie",
    "retry-after",
    "x-accel-buffering",
}


def create_app(api_url):
    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=5), trust_env=False
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, openapi_url=None)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(path: str, request: Request):
        url = f"{api_url.rstrip('/')}/api/{path}"
        if request.url.query:
            url += "?" + request.url.query
        upstream = request.app.state.client.build_request(
            request.method,
            url,
            headers={k: v for k, v in request.headers.items() if k in REQUEST_HEADERS},
            content=request.stream(),
        )
        try:
            response = await request.app.state.client.send(upstream, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"detail": "API 服务不可连接，请启动独立后端。"}, status_code=502)
        return StreamingResponse(
            response.aiter_bytes(),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k in RESPONSE_HEADERS},
            background=BackgroundTask(response.aclose),
        )

    app.mount("/gateway", StaticFiles(directory=FRONTEND, html=True), name="gateway-assets")
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()
    uvicorn.run(create_app(args.api), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
