"""Run the AlphaMaxx server: python -m alphamaxx"""

import uvicorn

from alphamaxx.config import settings


def main() -> None:
    uvicorn.run(
        "alphamaxx.web.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.LIVE_RELOAD,
    )


if __name__ == "__main__":
    main()
