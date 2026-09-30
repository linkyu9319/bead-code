"""本地启动入口；生产环境请使用 Docker Compose。"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="0.0.0.0", port=8000)
