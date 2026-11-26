from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets

logger = logging.getLogger(__name__)


def _redact_ws_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if not host:
            return "<redacted-ws-url>"
        authority = f"[{host}]" if ":" in host else host
        try:
            if parts.port is not None:
                authority = f"{authority}:{parts.port}"
        except ValueError:
            pass
        return urlunsplit((parts.scheme, authority, parts.path, "", ""))
    except Exception:
        return "<redacted-ws-url>"


EventHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class OneBotWSClient:
    """Lightweight async OneBot v11 forward WebSocket client (e.g., NapCat / LLOneBot)."""

    def __init__(
        self,
        ws_url: str,
        *,
        access_token: str = "",
        reconnect_interval_sec: float = 5.0,
        action_timeout_sec: float = 15.0,
        max_concurrency: int = 16,
        max_pending_events: int = 256,
    ) -> None:
        self.ws_url = ws_url
        self.access_token = access_token
        self.reconnect_interval_sec = reconnect_interval_sec
        self.action_timeout_sec = action_timeout_sec
        self._max_concurrency = max(1, int(max_concurrency))
        self._max_pending_events = max(1, int(max_pending_events))
        self._ws: Any = None
        self._pending_actions: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._stop_event = asyncio.Event()
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_pending_events)

    def stop(self) -> None:
        self._stop_event.set()

    async def call_action(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("OneBot WebSocket is not connected.")
        echo = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_actions[echo] = fut
        payload = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
            resp = await asyncio.wait_for(fut, timeout=self.action_timeout_sec)
            if resp.get("status") == "failed":
                raise RuntimeError(f"OneBot action {action} failed: {resp}")
            return resp.get("data") if isinstance(resp.get("data"), dict) else resp
        finally:
            self._pending_actions.pop(echo, None)

    async def get_msg(self, message_id: str | int) -> dict[str, Any]:
        mid_str = str(message_id)
        mid_val: int | str = int(mid_str) if mid_str.lstrip("-").isdigit() else mid_str
        return await self.call_action("get_msg", {"message_id": mid_val})

    async def get_forward_msg(self, forward_id: str) -> list[dict[str, Any]]:
        data = await self.call_action("get_forward_msg", {"id": forward_id, "message_id": forward_id})
        messages = data.get("messages") or data.get("message") or []
        return messages if isinstance(messages, list) else []

    def _handle_incoming_payload(self, raw: str, on_event: EventHandler) -> None:
        del on_event  # Dispatched by bounded worker pool started in run_forever()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring non-JSON frame from OneBot WS")
            return
        if not isinstance(payload, dict):
            return

        # Action response correlation
        echo = payload.get("echo")
        if echo and str(echo) in self._pending_actions:
            fut = self._pending_actions[str(echo)]
            if not fut.done():
                fut.set_result(payload)
            return

        # Meta/heartbeat events do not need pipeline dispatch
        if payload.get("post_type") == "meta_event":
            return

        try:
            self._event_queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning(
                "OneBot event queue full (max=%d); dropping incoming event",
                self._max_pending_events,
            )

    async def _worker_loop(self, on_event: EventHandler) -> None:
        while True:
            event = await self._event_queue.get()
            try:
                await on_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unhandled error while processing OneBot event")
            finally:
                self._event_queue.task_done()

    async def run_forever(self, on_event: EventHandler) -> None:
        backoff = self.reconnect_interval_sec
        headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else None
        workers = [
            asyncio.create_task(self._worker_loop(on_event))
            for _ in range(self._max_concurrency)
        ]

        try:
            while not self._stop_event.is_set():
                try:
                    logger.info("Connecting to OneBot v11 WebSocket: %s", _redact_ws_url(self.ws_url))
                    async with websockets.connect(self.ws_url, additional_headers=headers) as ws:
                        self._ws = ws
                        backoff = self.reconnect_interval_sec
                        logger.info("Connected to OneBot v11 WebSocket")
                        async for raw in ws:
                            if self._stop_event.is_set():
                                break
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8", errors="ignore")
                            self._handle_incoming_payload(raw, on_event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "OneBot WS disconnected (%s). Reconnecting in %.1fs...",
                        type(exc).__name__,
                        backoff,
                    )
                finally:
                    self._ws = None
                    for fut in self._pending_actions.values():
                        if not fut.done():
                            fut.set_exception(ConnectionError("OneBot WebSocket disconnected"))
                    self._pending_actions.clear()

                if not self._stop_event.is_set():
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
