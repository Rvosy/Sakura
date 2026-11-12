import pytest

from sakura_assistant_contract import ScreenObservation
from sakura_assistant.agent.screen_observation import (
    build_manual_screen_observation_batch_user_message,
    build_screen_observation_batch_user_message,
    build_screen_observation_user_message,
)
from sakura_assistant.llm.api_client import _model_request
from sakura_openai_responses import build_request
from plugins.builtin.sakura_model_openai_compatible.transport import _message


@pytest.mark.parametrize("source", ["manual", "proactive", "single"])
def test_screen_images_reach_both_model_protocols_with_high_detail(source):
    observation = ScreenObservation(
        data_url="data:image/jpeg;base64,AA==",
        width=3840,
        height=2160,
        captured_at="2026-11-13T00:00:00Z",
        screen_name="fixture",
    )
    if source == "single":
        message = build_screen_observation_user_message("读取文字", observation)
    else:
        builder = (build_manual_screen_observation_batch_user_message if source == "manual"
                   else build_screen_observation_batch_user_message)
        message = builder("读取文字", (observation,))
    request = _model_request({"messages": [message]})
    chat_image = _message(request["messages"][0])["content"][1]["image_url"]
    responses_image = build_request("fixture", request)["input"][0]["content"][1]
    assert chat_image == {"url": observation.data_url, "detail": "high"}
    assert responses_image == {
        "type": "input_image", "image_url": observation.data_url, "detail": "high",
    }
