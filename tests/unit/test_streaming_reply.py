from __future__ import annotations

import json

from app.llm.chat_reply import ChatSegment
from app.llm.streaming_reply import (
    StreamedReplyParser,
    is_streaming_candidate,
    needs_streaming_translation_repair,
)


def test_parser_streams_jsonl_when_first_chunk_is_only_open_brace() -> None:
    parser = StreamedReplyParser(["中性"])

    assert parser.feed("{") == []
    segments = parser.feed(
        '"ja":"ちゃんと聞いてるよ。","zh":"我在认真听。",'
        '"tone":"中性","portrait":"站立待机"}\n'
    )

    assert segments == [
        ChatSegment(
            "ちゃんと聞いてるよ。",
            "中性",
            "我在认真听。",
            "站立待机",
        )
    ]
    assert parser.finish() == []


def test_parser_accepts_pretty_structured_json_at_finish() -> None:
    parser = StreamedReplyParser(["中性"])
    payload = json.dumps(
        {
            "segments": [
                {
                    "ja": "大丈夫だよ。",
                    "zh": "没关系。",
                    "tone": "中性",
                    "portrait": "站立待机",
                }
            ]
        },
        ensure_ascii=False,
        indent=2,
    )

    assert parser.feed(payload) == []
    assert parser.finish() == [
        ChatSegment("大丈夫だよ。", "中性", "没关系。", "站立待机")
    ]


def test_parser_keeps_user_facing_lines_that_mention_protocol_terms() -> None:
    parser = StreamedReplyParser(["中性"])

    segments = parser.feed(
        "「segments」は内部字段だよ。 || 「segments」是内部字段。 || 中性 || 站立待机\n"
    )

    assert segments == [
        ChatSegment(
            "「segments」は内部字段だよ。",
            "中性",
            "「segments」是内部字段。",
            "站立待机",
        )
    ]


def test_parser_drops_incomplete_json_tail_instead_of_showing_protocol_text() -> None:
    parser = StreamedReplyParser(["中性"])
    first = parser.feed(
        '{"ja":"うん。","zh":"嗯。","tone":"中性"}\n'
        '{"ja":"途中で切れた。"'
    )

    assert first == [ChatSegment("うん。", "中性", "嗯。")]
    assert parser.finish() == []


def test_streaming_candidate_is_conservative_about_tools_memory_and_media() -> None:
    assert is_streaming_candidate(
        [{"role": "user", "content": "Sakura，今天陪我聊一会儿"}]
    )
    assert not is_streaming_candidate(
        [{"role": "user", "content": "你还记得我上次说的偏好吗？"}]
    )
    assert not is_streaming_candidate(
        [{"role": "user", "content": "看看这个页面：https://example.com"}]
    )
    assert not is_streaming_candidate(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看看这张图"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    },
                ],
            }
        ]
    )


def test_translation_repair_only_targets_japanese_without_chinese_subtitle() -> None:
    assert needs_streaming_translation_repair(ChatSegment("こんにちは。"))
    assert needs_streaming_translation_repair(
        ChatSegment("こんにちは。", translation="こんにちは。")
    )
    assert not needs_streaming_translation_repair(
        ChatSegment("こんにちは。", translation="你好。")
    )
    assert not needs_streaming_translation_repair(
        ChatSegment("你好。", translation="你好。")
    )
