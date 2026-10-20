import logging
import os


def setup(name: str) -> logging.Logger:
    logging.basicConfig(
        level=os.environ.get("EMS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger(name)
