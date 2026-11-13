"""Central logging setup."""
import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Keep third-party libraries quieter.
    for noisy in ("httpx", "httpcore", "openai", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
