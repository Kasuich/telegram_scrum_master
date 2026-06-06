from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("telegram_gateway.main:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
