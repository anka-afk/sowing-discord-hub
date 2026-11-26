from __future__ import annotations

import pytest

from sowing_collector.config import TriggerConfig
from sowing_collector.desensitizer import BlockedContentError, desensitize_onebot_message
from sowing_collector.triggers import TriggerEngine


def test_trigger_emoji_like_net_threshold():
    engine = TriggerEngine(TriggerConfig(emoji_like_threshold=3), watch_groups=[1001])

    # Unwatched group ignored
    assert (
        engine.process_event(
            {
                "post_type": "notice",
                "notice_type": "group_msg_emoji_like",
                "group_id": 9999,
                "message_id": 10,
                "likes": [{"emoji_id": "128077", "count": 10}],
            }
        )
        is None
    )

    # Incremental count below threshold
    assert (
        engine.process_event(
            {
                "post_type": "notice",
                "notice_type": "group_msg_emoji_like",
                "group_id": 1001,
                "message_id": 55,
                "likes": [{"emoji_id": "128077", "count": 2}],
            }
        )
        is None
    )

    # Another emoji pushes net likes to 3 -> triggers
    cand = engine.process_event(
        {
            "post_type": "notice",
            "notice_type": "group_msg_emoji_like",
            "group_id": 1001,
            "message_id": 55,
            "likes": [{"emoji_id": "128514", "count": 1}],
        }
    )
    assert cand is not None
    assert cand.trigger_source == "emoji_like"
    assert cand.group_id == 1001
    assert cand.message_id == "55"


def test_trigger_essence_and_hashtag_reply():
    engine = TriggerEngine(TriggerConfig(), watch_groups=[1001])

    # Essence add triggers, delete ignored
    assert (
        engine.process_event(
            {
                "post_type": "notice",
                "notice_type": "essence",
                "sub_type": "delete",
                "group_id": 1001,
                "message_id": 77,
            }
        )
        is None
    )
    ess = engine.process_event(
        {
            "post_type": "notice",
            "notice_type": "essence",
            "sub_type": "add",
            "group_id": 1001,
            "message_id": 77,
            "sender_id": 88888,
        }
    )
    assert ess is not None and ess.trigger_source == "essence" and ess.message_id == "77"

    # Inline #投稿 with tags
    inline = engine.process_event(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 1001,
            "message_id": 88,
            "user_id": 123456,
            "message": [{"type": "text", "data": {"text": "#投稿 #沙雕 #日常 今天发生一件离谱事"}}],
        }
    )
    assert inline is not None
    assert inline.trigger_source == "hashtag"
    assert inline.extra_tags == ["沙雕", "日常"]
    assert inline.inline_segments == [{"type": "text", "data": {"text": "今天发生一件离谱事"}}]

    # Reply + #投稿 nominates replied message
    reply_cand = engine.process_event(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 1001,
            "message_id": 90,
            "user_id": 123456,
            "message": [
                {"type": "reply", "data": {"id": "77"}},
                {"type": "text", "data": {"text": "#投稿 #典中典"}},
            ],
        }
    )
    assert reply_cand is not None
    assert reply_cand.message_id == "77"
    assert reply_cand.inline_segments is None
    assert reply_cand.extra_tags == ["典中典"]


def test_desensitizer_flattens_chat_record_with_limits_and_anonymizes():
    # Build 25 nodes with a nested depth-1 and depth-2 forward
    raw_nodes = []
    for i in range(22):
        raw_nodes.append(
            {
                "type": "node",
                "data": {
                    "user_id": 100000 + (i % 3),
                    "nickname": f"真实昵称{i}",
                    "time": 1700000000 + i * 60,
                    "content": [
                        {"type": "at", "data": {"qq": "100001"}},
                        {"type": "text", "data": {"text": f"消息内容 {i}"}},
                    ],
                },
            }
        )
    # Add depth-1 nested forward on first node, containing a depth-2 forward
    raw_nodes[0]["data"]["content"].append(
        {
            "type": "forward",
            "data": {
                "content": [
                    {
                        "data": {
                            "user_id": 999999,
                            "nickname": "嵌套用户",
                            "content": [
                                {"type": "text", "data": {"text": "第一层嵌套消息"}},
                                {
                                    "type": "forward",
                                    "data": {
                                        "content": [
                                            {"data": {"user_id": 777777, "content": [{"type": "text", "data": {"text": "第二层"}}]}}
                                        ]
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
        }
    )

    res = desensitize_onebot_message(
        [{"type": "forward", "data": {"id": "fwd-1"}}],
        group_id=55555,
        sender_user_id=123456789,
        submitter_salt="secret-salt",
        forward_nodes_map={"fwd-1": raw_nodes},
        max_chat_depth=1,
        max_chat_nodes=20,
    )
    assert len(res.blocks) == 1
    chat_block = res.blocks[0]
    assert chat_block["type"] == "chat_record"
    msgs = chat_block["messages"]
    assert len(msgs) == 20  # Hard cap max_nodes=20

    # Ensure zero real QQ or real nickname leakage
    serialized = str(chat_block)
    assert "真实昵称" not in serialized
    assert "100000" not in serialized
    assert "100001" not in serialized
    assert "999999" not in serialized
    assert "群友A" in serialized
    assert "群友B" in serialized
    assert "[嵌套聊天记录已折叠]" in serialized
    assert "123456789" not in res.submitter_hash


def test_desensitizer_blocks_video_and_allows_whitelisted_video_link():
    with pytest.raises(BlockedContentError) as exc:
        desensitize_onebot_message(
            [{"type": "video", "data": {"file": "clip.mp4"}}],
            group_id=1001,
            sender_user_id=10001,
            submitter_salt="salt",
        )
    assert exc.value.code == "video_file_not_allowed"

    res = desensitize_onebot_message(
        [{"type": "text", "data": {"text": "爆笑视频 https://www.bilibili.com/video/BV1xx411c7mD"}}],
        group_id=1001,
        sender_user_id=10001,
        submitter_salt="salt",
        video_link_whitelist=["bilibili.com", "b23.tv"],
    )
    types = [b["type"] for b in res.blocks]
    assert "text" in types
    assert "video_link" in types
    vl = [b for b in res.blocks if b["type"] == "video_link"][0]
    assert vl["platform"] == "bilibili"
    assert vl["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"
