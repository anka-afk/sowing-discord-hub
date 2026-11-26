from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from sowing_collector.config import load_config
from sowing_collector.dedup import DedupStore
from sowing_collector.hub_client import HubCollectorClient
from sowing_collector.onebot_ws import OneBotWSClient
from sowing_collector.pipeline import CollectorPipeline

logger = logging.getLogger("sowing_collector")


async def run_collector(config_path: str | Path | None) -> None:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    dedup = DedupStore(
        db_path=config.storage.db_path,
        phash_threshold=config.filters.phash_hamming_threshold,
    )
    ws_client = OneBotWSClient(
        ws_url=config.onebot.ws_url,
        access_token=config.onebot.access_token,
        reconnect_interval_sec=config.onebot.reconnect_interval_sec,
        action_timeout_sec=config.onebot.action_timeout_sec,
    )

    async with HubCollectorClient(
        base_url=config.hub.base_url,
        shared_secret=config.hub.shared_secret,
        timeout_sec=config.hub.timeout_sec,
    ) as hub_client:
        pipeline = CollectorPipeline(
            config=config,
            hub_client=hub_client,
            dedup_store=dedup,
            get_msg=ws_client.get_msg,
            get_forward_msg=ws_client.get_forward_msg,
        )

        async def _on_event(event: dict) -> None:
            outcome = await pipeline.handle_event(event)
            if outcome.accepted:
                logger.info("Ingested submission %s (%s)", outcome.submission_id, outcome.reason)
            elif outcome.reason != "not_triggered":
                logger.info("Skipped candidate: %s", outcome.reason)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, ws_client.stop)
            except NotImplementedError:
                pass

        try:
            await ws_client.run_forever(_on_event)
        finally:
            dedup.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AstrBot Sowing Discord v2 Official Group Collector")
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to collector YAML configuration file (default: config.yaml)",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(run_collector(args.config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
