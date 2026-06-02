import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("PM Orchestrator started")
    # placeholder: agent loop will go here
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
