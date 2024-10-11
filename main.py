import os
import json
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import aiohttp

# Load environment variables from .env file
load_dotenv()

# Retrieve the OpenAI API key from environment variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

if not OPENAI_API_KEY:
    raise RuntimeError('Missing OpenAI API key. Please set it in the .env file.')

# Initialize FastAPI
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
SYSTEM_MESSAGE = 'You are an AI receptionist for Barts Automotive. Your job is to politely engage with the client and obtain their name, availability, and service/work required. Ask one question at a time...'
VOICE = 'alloy'
PORT = int(os.getenv('PORT', 5050))
WEBHOOK_URL = "<input your webhook URL here>"

# Session management
sessions = {}

# Event types to log to the console
LOG_EVENT_TYPES = [
    'response.content.done', 'rate_limits.updated', 'response.done',
    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started', 'session.created',
    'response.text.done', 'conversation.item.input_audio_transcription.completed'
]

# Root route
@app.get("/")
async def root():
    return {"message": "Twilio Media Stream Server is running!"}

# Route for Twilio to handle incoming and outgoing calls
@app.post("/incoming-call")
async def incoming_call(request: Request):
    twiml_response = f"""
    <?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say>Hi, you have called Bart's Automotive Centre. How can we help?</Say>
        <Connect>
            <Stream url="wss://{request.client.host}/media-stream" />
        </Connect>
    </Response>
    """
    return Response(content=twiml_response, media_type="text/xml")

# WebSocket route for media stream
@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    session_id = websocket.headers.get('x-twilio-call-sid', f'session_{int(asyncio.get_event_loop().time())}')
    session = sessions.get(session_id, {"transcript": "", "streamSid": None})
    sessions[session_id] = session

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
    ) as openai_ws:

        # Send session update
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
                "input_audio_transcription": {"model": "whisper-1"}
            }
        }

        await asyncio.sleep(0.25)
        await openai_ws.send(json.dumps(session_update))

        async for message in openai_ws:
            data = json.loads(message)
            if data.get('type') in LOG_EVENT_TYPES:
                print(f"Received event: {data['type']}")

            # Handle transcription
            if data.get('type') == 'conversation.item.input_audio_transcription.completed':
                user_message = data['transcript'].strip()
                session['transcript'] += f"User: {user_message}\n"
                print(f"User ({session_id}): {user_message}")

            # Handle agent response
            if data.get('type') == 'response.done':
                agent_message = next(
                    (content['transcript'] for content in data['response']['output'][0]['content'] if 'transcript' in content),
                    'Agent message not found'
                )
                session['transcript'] += f"Agent: {agent_message}\n"
                print(f"Agent ({session_id}): {agent_message}")

        await process_transcript_and_send(session['transcript'], session_id)

# Function to send data to a webhook
async def send_to_webhook(payload):
    async with aiohttp.ClientSession() as session:
        async with session.post(WEBHOOK_URL, json=payload) as resp:
            if resp.status == 200:
                print('Data successfully sent to webhook')
            else:
                print(f"Failed to send data to webhook: {resp.status}")

# Function to process and send transcript
async def process_transcript_and_send(transcript, session_id=None):
    result = await make_chatgpt_completion(transcript)
    if 'choices' in result:
        parsed_content = json.loads(result['choices'][0]['message']['content'])
        await send_to_webhook(parsed_content)

# Function to call OpenAI API for completions
async def make_chatgpt_completion(transcript):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            'https://api.openai.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'},
            json={
                "model": "gpt-4o-2024-08-06",
                "messages": [
                    {"role": "system", "content": "Extract customer details: name, availability, and any special notes from the transcript."},
                    {"role": "user", "content": transcript}
                ]
            }
        ) as resp:
            return await resp.json()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
