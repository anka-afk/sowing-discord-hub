"""Cold-start historical seeder: pulls recent messages from watched QQ groups and ingests into Hub."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets

from sowing_collector.config import load_config
from sowing_collector.dedup import DedupStore
from sowing_collector.desensitizer import normalize_segments
from sowing_collector.hub_client import HubCollectorClient
from sowing_collector.pipeline import CollectorPipeline
from sowing_collector.triggers import TriggerCandidate

logger = logging.getLogger("sowing_collector.seed")


def _build_ws_url(ws_url: str, access_token: str) -> str:
    if not access_token:
        return ws_url
    parsed = urlparse(ws_url)
    query = dict(parse_qsl(parsed.query))
    query.setdefault("access_token", access_token)
    return urlunparse(parsed._replace(query=urlencode(query)))


async def _call_onebot(ws: Any, action: str, params: dict[str, Any], echo: str, timeout: float = 20.0) -> dict[str, Any]:
    await ws.send(json.dumps({"action": action, "params": params, "echo": echo}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        data = json.loads(raw)
        if data.get("echo") == echo:
            return data


async def seed_groups(config_path: str, per_group: int = 15, max_total_accepted: int = 60) -> dict[str, Any]:
    cfg = load_config(config_path)
    dedup = DedupStore(db_path=cfg.storage.db_path, phash_threshold=cfg.filters.phash_hamming_threshold)
    ws_url = _build_ws_url(cfg.onebot.ws_url, cfg.onebot.access_token)

    stats = Counter()
    accepted_ids: list[str] = []
    echo_seq = 0

    def _next_echo(prefix: str) -> str:
        nonlocal echo_seq
        echo_seq += 1
        return f"{prefix}_{echo_seq}"

    async with websockets.connect(ws_url, ping_interval=None, open_timeout=10, max_size=32 * 1024 * 1024) as ws:
        async def get_msg(mid: str) -> dict[str, Any]:
            res = await _call_onebot(ws, "get_msg", {"message_id": int(mid) if str(mid).lstrip("-").isdigit() else mid}, _next_echo("getmsg"))
            return res.get("data") or {}

        async def get_forward_msg(fid: str) -> list[dict[str, Any]]:
            res = await _call_onebot(ws, "get_forward_msg", {"id": fid, "message_id": fid}, _next_echo("fwd"))
            data = res.get("data") or {}
            return data.get("messages") or data.get("message") or []

        async with HubCollectorClient(
            base_url=cfg.hub.base_url,
            shared_secret=cfg.hub.shared_secret,
            timeout_sec=cfg.hub.timeout_sec,
        ) as hub_client:
            pipeline = CollectorPipeline(
                cfg,
                hub_client,
                dedup,
                get_msg=get_msg,
                get_forward_msg=get_forward_msg,
            )

            for gid in cfg.onebot.watch_groups:
                if len(accepted_ids) >= max_total_accepted:
                    break
                try:
                    hist = await _call_onebot(
                        ws,
                        "get_group_msg_history",
                        {"group_id": int(gid), "count": per_group},
                        _next_echo(f"hist_{gid}"),
                    )
                except Exception as exc:
                    logger.warning("Failed history fetch for group %s: %s", gid, exc)
                    stats["history_fetch_error"] += 1
                    continue

                msgs = (hist.get("data") or {}).get("messages") or []
                logger.info("Group %s: fetched %d historical messages", gid, len(msgs))
                for m in msgs:
                    if len(accepted_ids) >= max_total_accepted:
                        break
                    mid = m.get("message_id")
                    if mid is None:
                        continue
                    segs = normalize_segments(m.get("message") or m.get("raw_message") or [])
                    if not segs:
                        continue
                    has_media_or_forward = any(s.get("type") in ("image", "forward") for s in segs)
                    plain_len = sum(
                        len(str(s.get("data", {}).get("text", "")).strip())
                        for s in segs
                        if s.get("type") == "text"
                    )
                    if not has_media_or_forward and plain_len < cfg.triggers.ingest_all_min_text_without_media:
                        stats["skipped_noise"] += 1
                        continue

                    candidate = TriggerCandidate(
                        trigger_source="cold_start_history",
                        group_id=int(gid),
                        message_id=str(mid),
                        sender_user_id=m.get("user_id") or 0,
                        inline_segments=segs,
                        extra_tags=[],
                    )
                    try:
                        outcome = await pipeline.process_candidate(candidate)
                    except Exception as exc:
                        logger.warning("Error seeding group=%s mid=%s: %s", gid, mid, exc)
                        stats[f"error:{type(exc).__name__}"] += 1
                        continue
                    if outcome.accepted:
                        stats["accepted"] += 1
                        if outcome.submission_id:
                            accepted_ids.append(outcome.submission_id)
                        logger.info("Seeded %s (group=%s mid=%s)", outcome.submission_id, gid, mid)
                    else:
                        stats[f"skip:{outcome.reason}"] += 1

    dedup.close()
    return {"accepted_count": len(accepted_ids), "accepted_ids": accepted_ids, "stats": dict(stats)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cold-start seed Hub from watched QQ groups")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--per-group", type=int, default=15)
    parser.add_argument("--max-total", type=int, default=50)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = asyncio.run(seed_groups(args.config, per_group=args.per_group, max_total_accepted=args.max_total))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
