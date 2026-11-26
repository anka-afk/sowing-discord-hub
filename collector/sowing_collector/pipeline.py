from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .ac_filter import AhoCorasickFilter
from .config import CollectorConfig
from .dedup import DedupStore, compute_text_hash
from .desensitizer import (
    BlockedContentError,
    desensitize_onebot_message,
    normalize_segments,
)
from .hub_client import HubCollectorClient
from .media import MediaValidationError, download_media, validate_media_bytes
from .triggers import TriggerCandidate, TriggerEngine

logger = logging.getLogger(__name__)


def _redact_url_for_log(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if not host:
            return "<redacted-url>"
        authority = f"[{host}]" if ":" in host else host
        try:
            if parts.port is not None:
                authority = f"{authority}:{parts.port}"
        except ValueError:
            pass
        return urlunsplit((parts.scheme, authority, parts.path, "", ""))
    except Exception:
        return "<redacted-url>"


def _hash_source_msg_id(group_id: int, message_id: str, salt: str) -> str:
    digest = hmac.new(
        salt.encode("utf-8"),
        f"source_msg:{group_id}:{message_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"msg_{digest[:32]}"

GetMsgFn = Callable[[str], Awaitable[dict[str, Any]]]
GetForwardMsgFn = Callable[[str], Awaitable[list[dict[str, Any]]]]
DownloadMediaFn = Callable[[str, int], Awaitable[bytes]]


@dataclass
class ProcessOutcome:
    accepted: bool
    reason: str
    submission_id: str | None = None
    duplicate_early_stop: bool = False


class CollectorPipeline:
    """Orchestrates trigger evaluation, free local screening, desensitization, and Hub HMAC ingest."""

    def __init__(
        self,
        config: CollectorConfig,
        hub_client: HubCollectorClient,
        dedup_store: DedupStore | None = None,
        *,
        get_msg: GetMsgFn | None = None,
        get_forward_msg: GetForwardMsgFn | None = None,
        media_downloader: DownloadMediaFn | None = None,
    ) -> None:
        self.config = config
        self.hub_client = hub_client
        self.dedup = dedup_store or DedupStore(
            db_path=config.storage.db_path,
            phash_threshold=config.filters.phash_hamming_threshold,
        )
        self.triggers = TriggerEngine(
            config.triggers,
            watch_groups=config.onebot.watch_groups,
            allow_all_groups=config.onebot.allow_all_groups,
        )
        self.ac_filter = AhoCorasickFilter(config.load_all_sensitive_words())
        self._get_msg = get_msg
        self._get_forward_msg = get_forward_msg
        self._media_downloader = media_downloader or self._default_download

    async def _default_download(self, url: str, max_bytes: int) -> bytes:
        return await download_media(url, max_bytes=max_bytes, timeout_sec=self.config.hub.timeout_sec)

    async def handle_event(self, event: dict[str, Any]) -> ProcessOutcome:
        candidate = self.triggers.process_event(event)
        if candidate is None:
            return ProcessOutcome(accepted=False, reason="not_triggered")
        return await self.process_candidate(candidate)

    async def process_candidate(self, candidate: TriggerCandidate) -> ProcessOutcome:
        # 1. Message-level dedup (avoid duplicate ingest from multiple triggers on same message)
        if self.dedup.is_message_seen(candidate.group_id, candidate.message_id):
            return ProcessOutcome(accepted=False, reason="message_already_processed")

        segments = candidate.inline_segments
        sender_id = candidate.sender_user_id

        # Fetch original message via OneBot get_msg if triggered by reaction/essence/reply
        if segments is None:
            if self._get_msg is None:
                return ProcessOutcome(accepted=False, reason="missing_get_msg_provider")
            try:
                msg_data = await self._get_msg(candidate.message_id)
            except Exception as exc:
                logger.warning("Failed to fetch message %s: %s", candidate.message_id, exc)
                return ProcessOutcome(accepted=False, reason="get_msg_failed")
            segments = normalize_segments(msg_data.get("message") or msg_data.get("raw_message") or [])
            sender_info = msg_data.get("sender") if isinstance(msg_data.get("sender"), dict) else {}
            sender_id = msg_data.get("user_id") or sender_info.get("user_id") or sender_id

        if not segments:
            return ProcessOutcome(accepted=False, reason="empty_message")

        # Recursively resolve forward segments via get_forward_msg (up to max_chat_depth)
        forward_nodes_map: dict[str, list[dict[str, Any]]] = {}

        async def _prefetch_forwards(seg_list: list[dict[str, Any]], depth: int) -> None:
            if depth >= self.config.filters.max_chat_depth or not self._get_forward_msg:
                return
            for seg in seg_list:
                if not isinstance(seg, dict) or seg.get("type") != "forward":
                    continue
                sdata = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}
                fid = str(sdata.get("id") or "")
                nodes = sdata.get("content") or sdata.get("messages")
                if fid and not nodes and fid not in forward_nodes_map:
                    try:
                        nodes = await self._get_forward_msg(fid)
                        forward_nodes_map[fid] = nodes
                    except Exception as exc:
                        logger.warning("Failed to fetch forward msg %s (depth=%d): %s", fid, depth, exc)
                        continue
                if isinstance(nodes, list) and depth + 1 < self.config.filters.max_chat_depth:
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        ndata = node.get("data", node)
                        if not isinstance(ndata, dict):
                            continue
                        raw_c = ndata.get("content") if "content" in ndata else ndata.get("message", [])
                        await _prefetch_forwards(normalize_segments(raw_c), depth + 1)

        await _prefetch_forwards(segments, 0)

        # 2. Pre-desensitize & intercept video files
        try:
            desensitized = desensitize_onebot_message(
                segments,
                group_id=candidate.group_id,
                sender_user_id=sender_id,
                submitter_salt=self.config.hub.submitter_salt,
                forward_nodes_map=forward_nodes_map,
                video_link_whitelist=self.config.filters.video_link_whitelist,
                max_chat_depth=self.config.filters.max_chat_depth,
                max_chat_nodes=self.config.filters.max_chat_nodes,
                extra_tags=candidate.extra_tags,
                origin_label=f"官方投稿群({candidate.trigger_source})",
            )
        except BlockedContentError as exc:
            self.dedup.mark_message_seen(candidate.group_id, candidate.message_id, candidate.trigger_source)
            return ProcessOutcome(accepted=False, reason=exc.code)

        if not desensitized.blocks:
            return ProcessOutcome(accepted=False, reason="no_valid_blocks")

        # 3. AC sensitive word filter (free local check)
        combined_text = desensitized.combined_text()
        sensitive_hits = self.ac_filter.search(combined_text)
        if sensitive_hits:
            self.dedup.mark_message_seen(candidate.group_id, candidate.message_id, candidate.trigger_source)
            return ProcessOutcome(accepted=False, reason=f"sensitive_word:{sensitive_hits[0]}")

        # 4. Text length & image count bounds
        has_non_text = any(b.get("type") != "text" for b in desensitized.blocks)
        if not has_non_text and len(combined_text) < self.config.filters.min_text_length:
            return ProcessOutcome(accepted=False, reason="text_too_short")
        if len(combined_text) > self.config.filters.max_text_length:
            return ProcessOutcome(accepted=False, reason="text_too_long")
        if len(desensitized.pending_images) > self.config.filters.max_images_per_submission:
            return ProcessOutcome(accepted=False, reason="too_many_images")

        # 5. Text-only content SHA-256 dedup
        content_hash: str | None = None
        if not desensitized.pending_images and combined_text:
            content_hash = compute_text_hash(combined_text)
            if self.dedup.is_content_duplicate(content_hash):
                self.dedup.mark_message_seen(candidate.group_id, candidate.message_id, candidate.trigger_source)
                return ProcessOutcome(accepted=False, reason="duplicate_content", duplicate_early_stop=True)

        # 6. Download, validate, local SHA-256/pHash dedup, and upload each image via HMAC
        uploaded_media_records: list[tuple[str, str]] = []
        for target_block, img_ref in desensitized.pending_images:
            max_dl = self.config.filters.max_image_bytes
            try:
                raw_bytes = await self._media_downloader(img_ref.url, max_dl)
                validated = validate_media_bytes(
                    raw_bytes,
                    max_image_bytes=self.config.filters.max_image_bytes,
                    max_gif_bytes=self.config.filters.max_gif_bytes,
                    filename_hint=img_ref.file_hint,
                )
            except MediaValidationError as exc:
                self.dedup.mark_message_seen(candidate.group_id, candidate.message_id, candidate.trigger_source)
                return ProcessOutcome(accepted=False, reason=exc.code)
            except Exception as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    logger.warning(
                        "Failed to download media %s: HTTPStatusError(%s)",
                        _redact_url_for_log(img_ref.url),
                        exc.response.status_code,
                    )
                else:
                    logger.warning(
                        "Failed to download media %s: %s",
                        _redact_url_for_log(img_ref.url),
                        type(exc).__name__,
                    )
                return ProcessOutcome(accepted=False, reason="media_download_failed")

            local_dup = self.dedup.check_media_duplicate(validated.sha256, validated.phash)
            if local_dup.duplicate:
                self.dedup.mark_message_seen(candidate.group_id, candidate.message_id, candidate.trigger_source)
                return ProcessOutcome(
                    accepted=False,
                    reason=f"local_duplicate_media:{local_dup.match_type}",
                    duplicate_early_stop=True,
                )

            try:
                upload_resp = await self.hub_client.upload_media(
                    validated.data,
                    validated.filename,
                    validated.mime,
                )
            except Exception as exc:
                logger.warning("Failed to upload media to Hub: %s", exc)
                return ProcessOutcome(accepted=False, reason=f"hub_upload_failed:{type(exc).__name__}")
            if upload_resp.duplicate:
                self.dedup.mark_media_seen(validated.sha256, validated.phash)
                self.dedup.mark_message_seen(candidate.group_id, candidate.message_id, candidate.trigger_source)
                return ProcessOutcome(
                    accepted=False,
                    reason=f"hub_duplicate_media:{upload_resp.match_type or 'exact'}",
                    duplicate_early_stop=True,
                )

            # Mutate block to reference uploaded media_id and exact detected type
            if target_block.get("type") in ("image", "gif"):
                target_block["type"] = "gif" if validated.is_gif else "image"
            target_block["media_id"] = upload_resp.media_id
            target_block["w"] = upload_resp.w or validated.width
            target_block["h"] = upload_resp.h or validated.height
            uploaded_media_records.append((validated.sha256, validated.phash))

        # 7. Call POST /api/v1/internal/collector/ingest with HMAC-SHA256 (never send raw QQ group_id!)
        payload: dict[str, Any] = {
            "title": desensitized.title or "",
            "tags": desensitized.tags,
            "blocks": desensitized.blocks,
            "submitter_hash": desensitized.submitter_hash,
            "source_msg_id": _hash_source_msg_id(
                candidate.group_id,
                candidate.message_id,
                self.config.hub.submitter_salt,
            ),
            "attribution": {
                "submitter_name": "匿名群友",
                "origin_label": desensitized.origin_label,
            },
        }
        try:
            resp = await self.hub_client.ingest_submission(payload)
        except Exception as exc:
            logger.warning("Failed to ingest submission to Hub: %s", exc)
            return ProcessOutcome(accepted=False, reason=f"hub_ingest_failed:{type(exc).__name__}")

        # Record in local dedup store only after successful ingest
        self.dedup.mark_message_seen(candidate.group_id, candidate.message_id, candidate.trigger_source)
        if content_hash:
            self.dedup.mark_content_seen(content_hash)
        for sha256, phash in uploaded_media_records:
            self.dedup.mark_media_seen(sha256, phash)

        if resp.get("duplicate"):
            return ProcessOutcome(
                accepted=False,
                reason="hub_duplicate_submission",
                submission_id=str(resp.get("submission_id") or "") or None,
                duplicate_early_stop=True,
            )

        submission_id = str(resp.get("submission_id") or resp.get("item_id") or resp.get("id") or "")
        return ProcessOutcome(accepted=True, reason="ingested", submission_id=submission_id or None)
