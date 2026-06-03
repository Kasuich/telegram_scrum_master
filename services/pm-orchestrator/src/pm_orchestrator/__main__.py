import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_yandex_llm() -> bool:
    """One minimal call to verify YandexGPT credentials on startup.

    Returns True on success, False on any failure. Does not crash the service.
    """
    try:
        from core.llm import Message, complete

        response = await complete([Message(role="user", content="1")])
        logger.info(
            "YandexGPT health check OK — model=%s tokens=%s latency=%dms",
            response.model,
            response.usage.total_tokens if response.usage else "?",
            response.latency_ms,
        )
        return True
    except Exception as exc:
        logger.error("YandexGPT health check FAILED: %s", exc)
        return False


async def main() -> None:
    logger.info("PM Orchestrator starting")

    # Verify LLM credentials once at startup; exit early if broken in production.
    llm_ok = await check_yandex_llm()
    if not llm_ok and os.getenv("ENVIRONMENT") == "production":
        logger.error("LLM unavailable in production — aborting startup")
        sys.exit(1)

    logger.info("PM Orchestrator ready")
    # placeholder: agent loop will go here
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
