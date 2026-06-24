"""启动入口：uvicorn app.main:app
（仅运行需 fastapi/uvicorn；自检与纵切测试不需要，见 tests/smoke.py）"""
from .api import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=False)
