import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
import uvicorn
import requests

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Read the menu
with open('menus/hanami/output_lunch.txt', 'r') as file:
    menu = file.read()

SYSTEM_MESSAGE = (
    f"You are a friendly receptionist at a restaurant taking orders. Below are the extracted content from the menu. At the end, you should repeat the order to the client and confirm their name, number, price (including the 15% tax), whether the order is going to be picked up or delivered and the corresponding time.\n {menu}")

VOICE = 'alloy'
LOG_EVENT_TYPES = [
    "response.content.done",
    "rate_limits.updated",
    "response.done",
    "input_audio_buffer.committed",
    "input_audio_buffer.speech_stopped",
    "input_audio_buffer.speech_started",
    "session.created",
    "response.text.done",
    "conversation.item.input_audio_transcription.completed",
]

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.api_route("/", methods=["GET", "POST"])
async def index_page():
    return "<h1>Server is up and running. </h1>"

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    response = VoiceResponse()
    response.say("Hi")
    # response.say("Hi, you have called Hanami Sushi. How can we help?")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await send_session_update(openai_ws)
        stream_sid = None
        transcript = ""

        async def receive_from_twilio():
            nonlocal stream_sid, transcript
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)

                    if data['event'] == 'media' and openai_ws.open:
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))

                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")

                    elif data['event'] == 'stop':
                        # Extract summary after call ends
                        print("Call ended. Extracting customer details...")
                        await make_chatgpt_completion(transcript)

            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            nonlocal stream_sid, transcript
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)

                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    if response['type'] == 'conversation.item.input_audio_transcription.completed':
                        user_message = response.get('transcript', '').strip()
                        transcript += f"User: {user_message}\n"
                        print(f"User: {user_message}")

                    if response['type'] == 'response.done':
                        outputs = response.get('response', {}).get('output', [{}])

                        if outputs:
                            agent_message = outputs[0].get('content', [{}])[0].get('transcript', '').strip()
                            
                        else:
                            agent_message = 'Agent message not found'
                            
                        transcript += f"Agent: {agent_message}\n"
                        print(f"Agent: {agent_message}")

                    if response['type'] == 'session.updated':
                        print(f'Session updated successfully: {response}')

                    # Handle 'speech_started' event
                    if response['type'] == 'input_audio_buffer.speech_started':
                        print("Speech Start:", response['type'])

                        # Clear ongoing speech on Twilio side
                        clear_event = {
                            "streamSid": stream_sid,
                            "event": "clear"
                        }
                        await websocket.send_json(clear_event)
                        print("Cancelling AI speech from the server")

                        # Send interrupt message to OpenAI
                        interrupt_message = {"type": "response.cancel"}
                        await openai_ws.send(json.dumps(interrupt_message))

                    if response['type'] == 'response.audio.delta' and response.get('delta'):
                        try:
                            audio_payload = base64.b64encode(
                                base64.b64decode(response['delta'])
                            ).decode('utf-8')
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            await websocket.send_json(audio_delta)
                        except Exception as e:
                            print(f"Error processing audio data: {e}")

            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_session_update(openai_ws):
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            "input_audio_transcription": {
            "model": "whisper-1"
        }
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))


async def make_chatgpt_completion(transcript):
    """Call the OpenAI API to extract customer details from the transcript."""
    print("Starting Transcript Summary...")
    url = "https://api.openai.com/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract customer details: name, phone number, pickup or "
                    "delivery time, and any special notes from the transcript."
                )
            },
            {"role": "user", "content": transcript}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"ChatGPT API response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print("Full ChatGPT API response:", json.dumps(data, indent=2))
            return data
        else:
            print(f"Error: {response.status_code} - {response.text}")
            response.raise_for_status()

    except Exception as e:
        print(f"Error making ChatGPT completion call: {e}")
        raise

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0")
