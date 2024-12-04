from pathlib import Path
from openai import OpenAI
from main import INITIAL_MESSAGE


def update_initial_message():
    client = OpenAI()
    speech_file_path = Path(__file__).parent / "initial message temp.mp3"


    with client.audio.speech.with_streaming_response.create(
    model="tts-1-hd",
    voice="alloy",
    input=INITIAL_MESSAGE
    ) as response:
        response.stream_to_file(speech_file_path)


if __name__ == "__main__":
    update_initial_message()