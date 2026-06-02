from fastapi import FastAPI

app = FastAPI(title="PM Agent Platform API")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
