import os
from database import *
import time
import uuid
import datetime
import aiohttp
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
import uvicorn
import logging

load_dotenv()

### ENVIRONMENT VARIABLES ###
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")


### LOGGING CONFIG ###
log_filename = datetime.datetime.now().strftime("logs_%Y_%m.log")

# Configure logging
if not os.path.exists("logs"):
    os.makedirs("logs")  # Create a "logs" directory if it doesn't exist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(f"logs/{log_filename}", mode='a'),  # Append to log file
        logging.StreamHandler()  # Also output logs to the console
    ]
)

logger = logging.getLogger("voice-assistant-app")

# Read the menu
with open('menus/hanami/output_lunch.txt', 'r') as file:
    menu = file.read()

RESTAURANT_ID = "rest-12345"  # Test restaurant ID

SYSTEM_MESSAGE = (
    f"You are a friendly receptionist at a restaurant taking orders. Below are the extracted content from the menu. At the end, you should repeat the order to the client and confirm their name, number, total price before tax, whether the order is going to be picked up or delivered and the corresponding time.\n {menu}")

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

@app.get("/health")
async def health_check():
    return Response(content="OK", status_code=200)

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.api_route("/", methods=["GET", "POST"])
async def index_page():
    return "<h1>Server is up and running. </h1>"

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    response = VoiceResponse()
    response.say("Hi, how can I help you today?")
    # response.say("Hi, you have called Hanami Sushi. How can we help?")
    connect = Connect()
    connect.stream(url=f'wss://angelsbot.net/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.api_route("/incoming-message", methods=["GET", "POST"])
async def handle_incoming_message(request: Request):
    data = await request.json()
    logger.info(f'Incoming message: {data}')
    # Create a Twilio MessagingResponse object
    response = MessagingResponse()
    
    # Add a message to reply with "Hello"
    response.message("Hello")
    
    # Return the TwiML response as XML
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket, verbose = False):
    logger.info("Client connected")
    start_timer = time.time()
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await send_session_update(openai_ws)

        # # Add initial greeting to OpenAI for first response
        # initial_greeting = {
        #     "input": "Hello! Welcome to Hanami Sushi. How can I help you today?",
        #     "response_type": "audio"
        # }
        # await openai_ws.send(json.dumps(initial_greeting))  # Send initial greeting to OpenAI

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
                        logger.info(f"Incoming stream has started {stream_sid}")

                    elif data['event'] == 'stop':
                        # Extract summary after call ends
                        logger.info("Call ended. Extracting customer details...")
                        end_timer = time.time()
                        await process_transcript_and_send(transcript, end_timer - start_timer)

            except WebSocketDisconnect:
                logger.info("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

            except Exception as e:
                logger.error(f"Error in receive_from_twilio: {e}")

            finally:
                # Ensure OpenAI WebSocket is closed if still open
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            nonlocal stream_sid, transcript
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)

                    if response['type'] in LOG_EVENT_TYPES and verbose:
                        logger.info(f"Received event ({response['type']}): {response}")

                    if response['type'] == 'conversation.item.input_audio_transcription.completed':
                        user_message = response.get('transcript', '').strip()
                        transcript += f"User: {user_message}\n\n"
                        logger.info(f"User: {user_message}\n")

                    if response['type'] == 'response.done':
                        outputs = response.get('response', {}).get('output', [{}])

                        if outputs:
                            agent_message = outputs[0].get('content', [{}])[0].get('transcript', '').strip()
                            
                        else:
                            agent_message = 'Agent message not found'
                            
                        transcript += f"Agent: {agent_message}\n\n"
                        logger.info(f"Agent: {agent_message}\n")

                    if response['type'] == 'session.updated' and verbose:
                        logger.info(f'Session updated successfully: {response}')

                    # Handle 'speech_started' event
                    if response['type'] == 'input_audio_buffer.speech_started':
                        if verbose:
                            logger.info(f"Speech Start: {response['type']}")

                        # Clear ongoing speech on Twilio side
                        clear_event = {
                            "streamSid": stream_sid,
                            "event": "clear"
                        }
                        await websocket.send_json(clear_event)
                        logger.info("Cancelling AI speech from the server")

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
                            logger.error(f"Error processing audio data: {e}")

            except Exception as e:
                logger.error(f"Error in send_to_twilio: {e}")

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_session_update(openai_ws, verbose=False):
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
    
    if verbose:
        logger.info(f'Sending session update: {json.dumps(session_update)}')

    await openai_ws.send(json.dumps(session_update))


async def content_extraction(transcript, timer):
    """Make a ChatGPT API call and enforce schema using JSON."""
    logger.info("Starting ChatGPT API call...")

    # Generate unique call ID
    call_id = str(uuid.uuid4())
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    # Request payload with enforced JSON schema
    payload = {
        "model": "gpt-3.5-turbo",
        # "model": "gpt-4o-2024-08-06",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract the following details from the transcript: "
                    "name, phone number, order type (pickup or delivery) "
                    "and the pickup or delivery time. "
                    "Also, structure the order information in JSON format with items."
                    "For each item, also extract any relevant 'notes' from the transcript. If no notes are provided, leave it empty. "
                    "All information must be extracted from the given transcript below. If any is missing, simply leave it empty. Do not make up any information. "
                    "Also, note that if there is any conflicting information (such as for names, phone number, etc.), prefer the information from the agent, rather than the user. "
                    "Finally, determine if the order was placed, or if it was an mis-dial, or if the user hung up before finishing the order. Store this in a 'confirmation' key (as a boolean) if the order seems to have been placed by the user."
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
                        "name": {"type": "string"},
                        "phone_number": {"type": "string"},
                        "pickup": {"type": "boolean"},
                        "pickup_or_delivery_time": {"type": "string"},
                        "confirmation": {"type": "boolean"},  # Added confirmation key
                        "order_info": {
                            "type": "object",
                            "properties": {
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "quantity": {"type": "integer"},
                                            "unit_price": {"type": "number"},
                                            "notes": {"type": "string"} 
                                        },
                                        "required": ["name", "quantity", "unit_price", "notes"]
                                    }
                                },
                            },
                            "required": [
                                "items"
                            ]
                        }
                    },
                    "required": [
                        "name", "phone_number",
                        "pickup", "pickup_or_delivery_time", 
                        "confirmation", "order_info"
                    ]
                }
            }
        ],
        "function_call": {
            "name": "customer_order_extraction"
        }
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                logger.info(f"ChatGPT API response status: {response.status}")
                data = await response.json()
                logger.info(f"Full ChatGPT API response: {json.dumps(data, indent=2)}")

                # Parse the function call arguments
                arguments = json.loads(data["choices"][0]["message"]["function_call"]["arguments"])

                # Enrich the response with generated values
                arguments["call_id"] = call_id
                arguments["time_of_order"] = current_time
                arguments["timer"] = timer
                arguments['full_transcription'] = transcript #transcript with newlines

                # Add formatted order_id to order_info + required info for the order
                timestamp_seconds = int(datetime.datetime.now().timestamp())
                arguments["order_info"]["order_id"] = f"ORD-{timestamp_seconds}-{call_id[-5:]}"
                arguments['order_info']['timestamp'] = current_time
                arguments['order_info']['call_id'] = call_id
                arguments['order_info']['restaurant_id'] = RESTAURANT_ID
                arguments['order_info']['customer_name'] = arguments['name']
                arguments['order_info']['phone_number'] = arguments['phone_number']
                arguments['order_info']['pickup'] = arguments['pickup']
                arguments['order_info']['pickup_or_delivery_time'] = arguments['pickup_or_delivery_time']

                return arguments

        except Exception as error:
            logger.error(f"Error making ChatGPT completion call: {error}")
            raise


async def send_to_webhook(payload):
    """Send data to Make.com webhook."""
    logger.info(f"Sending data to webhook: {json.dumps(payload, indent=2)}")

    async with aiohttp.ClientSession() as session:
        try:
            # Send a POST request to the webhook URL
            async with session.post(
                WEBHOOK_URL,
                headers={"Content-Type": "application/json"},
                json=payload
            ) as response:

                logger.info(f"Webhook response status: {response.status}")
                if response.status == 200:
                    logger.info("Data successfully sent to webhook.")
                else:
                    logger.warning(f"Failed to send data to webhook: {response.reason}")

        except Exception as error:
            logger.error(f"Error sending data to webhook: {error}")

async def process_transcript_and_send(transcript, timer):
    """Process the transcript and send the extracted data to the webhook."""
    try:
        # Make the ChatGPT completion call
        result = await content_extraction(transcript, timer)

        # Check if the response contains the expected data
        if result:

            # Database connection setup
            connection = create_connection()

            # Insert call record
            call_id = result["call_id"]
            restaurant_id = RESTAURANT_ID
            transcript_text = result["full_transcription"]
            confirmation = result.get("confirmation", False)
            timestamp = result["time_of_order"]
            insert_call_record(connection, call_id, restaurant_id, 
                               timestamp, transcript_text, confirmation)

            # Insert order record if confirmed
            if confirmation:
                insert_order_record(connection, result["order_info"])
                insert_order_items(connection, result["order_info"]["items"], 
                                   result["order_info"]["order_id"])

            # Close database connection
            close_connection(connection)
            
            if not confirmation:
                return  # Stop if the order was not confirmed

            # Send order info to Lambda or other processes if needed
            await send_order_to_lambda(result["order_info"])

    except asyncio.TimeoutError:
        logger.warning("Timed out while processing the transcript.")
    except Exception as error:
        logger.error(f"Error in process_transcript_and_send: {error}")


async def send_order_to_lambda(order_info):
    """Asynchronously send order information to AWS Lambda via webhook and handle PDF response."""
    lambda_url = os.getenv("AWS_LAMBDA_URL")  # Get the Lambda URL from environment variables

    headers = {
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        try:
            logger.info("Sending order info to AWS Lambda...")
            async with session.post(lambda_url, headers=headers, json=order_info) as response:
                
                # If the response is successful (status code 200)
                if response.status == 200:
                    content_type = response.headers.get("Content-Type")
                    
                    # If the response is a PDF (application/pdf)
                    if content_type == "application/pdf":
                        # Read the PDF content as bytes
                        pdf_content = await response.read()

                        # Save the PDF to a local file
                        with open("receipt.pdf", "wb") as pdf_file:
                            pdf_file.write(pdf_content)

                        logger.info("PDF receipt successfully received and saved as 'receipt.pdf'.")
                    else:
                        logger.warning(f"Unexpected content type: {content_type}")
                
                else:
                    logger.warning(f"Failed to send order. Status Code: {response.status}")
                    text_response = await response.text()
                    logger.warning(f"Response: {text_response}")

        except aiohttp.ClientError as e:
            logger.error(f"Error sending order to Lambda: {e}")

# run app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0")
