import json

def extract_visual_observation_summary(content):
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    observation = value.get("visual_observation") if isinstance(value, dict) else None
    return observation if isinstance(observation, dict) else None
