import os
import uuid
import datetime
import aiohttp
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
import reportlab

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")

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
                        await process_transcript_and_send(transcript)

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
    """Make a ChatGPT API call and enforce schema using JSON."""
    print("Starting ChatGPT API call...")

    # Generate unique call ID
    call_id = str(uuid.uuid4())
    # Get current time as the time of the order
    current_time = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    # Request payload with enforced JSON schema
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract the following details from the transcript: "
                    "name, phone number, order type (pickup or delivery), "
                    "pickup or delivery time, and the full transcript. "
                    "Also, structure the order information in JSON format with items, subtotal, tax, and total."
                )
            },
            {"role": "user", "content": transcript}
        ],
        "functions": [
            {
                "name": "customer_order_extraction",
                "description": "Extracts customer order details from the transcript.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "call_id": {"type": "string"},
                        "name": {"type": "string"},
                        "phone_number": {"type": "string"},
                        "time_of_order": {"type": "string"},
                        "pickup": {"type": "boolean"},
                        "pickup_or_delivery_time": {"type": "string"},
                        "full_transcription": {"type": "string"},
                        "order_info": {
                            "type": "object",
                            "properties": {
                                "order_id": {"type": "string"},
                                "customer_name": {"type": "string"},
                                "timestamp": {"type": "string"},
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "quantity": {"type": "integer"},
                                            "unit_price": {"type": "number"}
                                        },
                                        "required": ["name", "quantity", "unit_price"]
                                    }
                                },
                                "subtotal": {"type": "number"},
                                "tax": {"type": "number"},
                                "total": {"type": "number"}
                            },
                            "required": [
                                "order_id", "customer_name", "timestamp",
                                "items", "subtotal", "tax", "total"
                            ]
                        }
                    },
                    "required": [
                        "call_id", "name", "phone_number", "time_of_order",
                        "pickup", "pickup_or_delivery_time", "full_transcription",
                        "order_info"
                    ]
                }
            }
        ],
        "function_call": {
            "name": "customer_order_extraction"
        }
    }

    # Make the API call using aiohttp
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                print(f"ChatGPT API response status: {response.status}")
                data = await response.json()
                print("Full ChatGPT API response:", json.dumps(data, indent=2))

                # Parse the function call arguments
                arguments = json.loads(data["choices"][0]["message"]["function_call"]["arguments"])

                # Example: Enrich the response with generated values
                arguments["call_id"] = call_id
                arguments["time_of_order"] = current_time

                # Add a formatted order_id to the order_info section
                timestamp_seconds = int(datetime.datetime.now().timestamp())
                arguments["order_info"]["order_id"] = f"ORD-{timestamp_seconds}-{call_id[-5:]}"

                # Send the order info to AWS Lambda
                await send_order_to_lambda(arguments["order_info"])

                return arguments

        except Exception as error:
            print(f"Error making ChatGPT completion call: {error}")
            raise




async def send_to_webhook(payload):
    """Send data to Make.com webhook."""
    print("Sending data to webhook:", json.dumps(payload, indent=2))

    async with aiohttp.ClientSession() as session:
        try:
            # Send a POST request to the webhook URL
            async with session.post(
                WEBHOOK_URL,
                headers={"Content-Type": "application/json"},
                json=payload
            ) as response:

                print(f"Webhook response status: {response.status}")
                if response.status == 200:
                    print("Data successfully sent to webhook.")
                else:
                    print(f"Failed to send data to webhook: {response.reason}")

        except Exception as error:
            print(f"Error sending data to webhook: {error}")

async def process_transcript_and_send(transcript):
    """Process the transcript and send the extracted data to the webhook."""

    try:
        # Make the ChatGPT completion call
        result = await make_chatgpt_completion(transcript)

        print("Processed result from ChatGPT:", json.dumps(result, indent=2))

        # Check if the response contains the expected data
        if result:
            try:
                # Send the parsed content to the webhook
                await send_to_webhook(result)
                print("Extracted and sent customer details:", result)

            except json.JSONDecodeError as parse_error:
                print(f"Error parsing JSON from ChatGPT response: {parse_error}")
        else:
            print("Unexpected response structure from ChatGPT API")
    except Exception as error:
        print(f"Error in process_transcript_and_send: {error}")


async def send_order_to_lambda(order_info):
    """Asynchronously send order information to AWS Lambda via webhook."""
    lambda_url = os.getenv("AWS_LAMBDA_URL")  # Get the Lambda URL from environment variables

    headers = {
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        try:
            print("Sending order info to AWS Lambda...")
            async with session.post(lambda_url, headers=headers, json=order_info) as response:
                if response.status == 200:
                    response_data = await response.json()
                    print("Order successfully sent to AWS Lambda!")
                    print("Lambda Response:", json.dumps(response_data, indent=2))
                else:
                    print(f"Failed to send order. Status Code: {response.status}")
                    print("Response:", await response.text())

        except aiohttp.ClientError as e:
            print(f"Error sending order to Lambda: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0")
