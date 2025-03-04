import os
import websockets
from websockets.exceptions import WebSocketException
import pytz
from printer import *
from pdf import *
from s3_handler import fetch_file_from_s3
from database import *
import time
import datetime
import aiohttp
import json
import base64
import asyncio
import websockets
from urllib.parse import parse_qs
from fastapi import FastAPI, WebSocket, Request, Response, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
import uvicorn
import logging
from audio import preprocess_audio
from pydub import AudioSegment
import audioop
load_dotenv()


### ENVIRONMENT VARIABLES ###
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
ENV = os.getenv('ENVIRONMENT', 'prod') 

### LOGGING CONFIG ###
log_filename = datetime.datetime.now().strftime("logs_%Y_%m.log")

# Configure logging
if not os.path.exists("logs"):
    os.makedirs("logs")  # Create a "logs" directory if it doesn't exist

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s - %(name)s - %(levelname)s - [ENV={ENV}] - %(message)s",
    handlers=[
        logging.FileHandler(f"logs/{log_filename}", mode='a'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("voice-assistant-app")
### END LOGGING CONFIG ###


VERBOSE = False
VERBOSE_TRANSCRIPT = False
RECORDING = True
VOICE = 'alloy'
MODEL_TEMPERATURE = 0.7 # must be [0.6, 1.2]
INITIAL_MESSAGE = "Hi, how can I help you today?"
INITIAL_MESSAGE_FR = "Bonjour, comment puis-je vous aider"
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
    "function_call",
    "response.function_call_arguments.done", 
    "conversation.item.created",
]

if ENV == 'local':
    WEBSOCKET_URL = os.getenv('WEBSOCKET_URL') # moved to .env file
    INCOMING_CALL_ENDPOINT = os.getenv('INCOMING_CALL_ENDPOINT')
    VERBOSE_TRANSCRIPT = True
else:
    WEBSOCKET_URL = os.getenv('WEBSOCKET_URL_PROD')
    INCOMING_CALL_ENDPOINT = os.getenv('INCOMING_CALL_ENDPOINT_PROD')

if not VERBOSE:
    logging.getLogger("twilio.http_client").setLevel(logging.WARNING)

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
    # Determine the content type of the request
    content_type = request.headers.get("Content-Type", "")
    parsed_data = {}

    if "application/json" in content_type:
        # Lambda-forwarded JSON object
        event = await request.json()
        body = event.get("body", "")
        is_base64_encoded = event.get("isBase64Encoded", False)

        # Decode Base64-encoded body if necessary
        if is_base64_encoded:
            decoded_bytes = base64.b64decode(body)
            decoded_body = decoded_bytes.decode("utf-8")
        else:
            decoded_body = body

        # Parse the URL-encoded body into a dictionary
        parsed_data = parse_qs(decoded_body)
        parsed_data = {key: value[0] for key, value in parsed_data.items()}

    elif "application/x-www-form-urlencoded" in content_type:
        # Direct Twilio request via Ngrok
        form_data = await request.form()
        parsed_data = {key: value for key, value in form_data.items()}
        logger.info(f"TESTING PARSED DATA: {json.dumps(parsed_data, indent=2)}")

    else:
        raise HTTPException(status_code=400, detail="Unsupported Content-Type")

    # Extract necessary Twilio parameters
    twilio_number = parsed_data.get("To", None)
    client_number = parsed_data.get("From", None)
    call_sid = parsed_data.get("CallSid", None)
    digits = parsed_data.get("Digits", None)  # Captured input from Gather

    if not twilio_number:
        raise HTTPException(status_code=400, detail="Missing 'To' parameter in the request")

    # Lookup restaurant ID based on Twilio number
    restaurant_id = get_restaurant_id_by_twilio_number(twilio_number)
    if not restaurant_id:
        raise HTTPException(status_code=404, detail="Restaurant not found for this number")

    restaurant_name = get_restaurant_name_by_restaurant_id(restaurant_id)
    if not restaurant_name:
        raise HTTPException(status_code=404, detail="Restaurant name not found for this restaurant")

    # Check subscription status
    subscription_status = get_subscription_status_by_restaurant_id(restaurant_id)
    if subscription_status == "cancelled":
        logger.info(f"Restaurant {restaurant_id} subscription is cancelled.")
        response = VoiceResponse()
        response.say("There is currently no active subscription on this account. Please contact support.")
        response.hangup()
        return HTMLResponse(content=str(response), media_type="application/xml")

    try:
        language = None

        if not digits:
            # Prompt user for language selection
            response = VoiceResponse()
            gather = response.gather(
                num_digits=1,
                action=INCOMING_CALL_ENDPOINT,  # Callback URL to handle selection
                method="POST"
            )
            # French part with a French voice
            gather.say(f"Bienvenue chez {restaurant_name}. Pour le français, appuyez sur le 1.", voice="alice", language="fr-CA")
            gather.pause(length=1)
            # English part with an English voice
            gather.say(f"Welcome to {restaurant_name}. For English, please press 2.", voice="alice", language="en-CA")
            response.append(gather)
            response.redirect(INCOMING_CALL_ENDPOINT)  # Redirect if no input is received
            return HTMLResponse(content=str(response), media_type="application/xml") 

        # Process language selection
        if digits == "1":
            language = "fr"
        elif digits == "2":
            language = "en"
        else:
            # Invalid input: prompt user again
            response = VoiceResponse()
            response.say("Invalid selection. Please try again.")
            response.redirect(INCOMING_CALL_ENDPOINT)
            return HTMLResponse(content=str(response), media_type="application/xml")

        # Construct WebSocket URL with language as part of the path
        websocket_url = f"{WEBSOCKET_URL}/{restaurant_id}/{client_number}/{call_sid}/{language}"

        # Create and append the Connect object
        response = VoiceResponse()
        connect = Connect()
        connect.stream(url=websocket_url)
        response.append(connect)

        return HTMLResponse(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error handling incoming call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error processing the call")

@app.websocket("/media-stream/{restaurant_id}/{client_number}/{call_sid}/{language}")
async def handle_media_stream(websocket: WebSocket, restaurant_id: int, 
                              client_number: str, call_sid: str, language: str):
    logger.info(f"{client_number} connected to media stream for restaurant_id: {restaurant_id}")

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        if VERBOSE:
            call = client.calls(call_sid).fetch()
            logger.info(f"Call status for SID {call_sid}: {call.status}")

            # Proceed only if the call is in progress
            if call.status != "in-progress":
                logger.error(f"Call SID {call_sid} is not in progress. Current status: {call.status}")
                raise Exception("Call is not in a valid state for recording")

        if RECORDING:
            # Enable recording for the call with a status callback
            recording = client.calls(call_sid).recordings.create(
                recording_status_callback="https://angelsbot.net/twilio-recording",
                recording_status_callback_method="POST",
                recording_status_callback_event=["completed"],  # Trigger on recording completion
                recording_channels="dual"  # Optional: record both sides of the conversation
            )
        
        # if VERBOSE:
        #     logger.info(f"Recording initiated for call SID {call_sid}. Recording SID: {recording.sid}")

    except Exception as e:
        logger.error(f"Failed to enable recording for call SID {call_sid}: {e}", exc_info=True)
    
    # Fetch the menu content from S3 using the new s3_handler
    menu_file_path = get_menu_file_path_by_restaurant_id(str(restaurant_id), language)

    if VERBOSE:
        logger.info(f'Menu file path: {menu_file_path}')
    if not menu_file_path:
        raise HTTPException(status_code=404, detail="Menu file path not found for this restaurant")
        
    try:
        wait_time = await calculate_wait_time()
        menu_content = fetch_file_from_s3(menu_file_path)

        system_message = await create_system_message(client_number, wait_time, menu_content, language)

    except Exception as e:
        logger.error(f"Failed to create system message menu: {e}")
        logger.info(f'\n{"-" * 75}\n')  # Logger separator
        raise HTTPException(status_code=500, detail="Failed to create system message menu from S3")

    # fetch the forward phone number
    forward_phone_number = get_forward_phone_number_by_restaurant_id(restaurant_id)
    if not forward_phone_number:
        logger.error(f'Forward phone number not found for restaurant id: {restaurant_id}, call sid: {call_sid}')
        raise HTTPException(status_code=404, detail="Forward phone number not found for this restaurant")
    
    
    start_timer = time.time()
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await send_session_update(openai_ws, system_message)

        stream_sid = None
        transcript = ""
        forward = False

        async def receive_from_twilio():
            nonlocal stream_sid, transcript, forward, language

            async def send_initial_message_audio():
                try:
                    # Load and process the MP3 file
                    if language == 'en':
                        initial_message_audio = AudioSegment.from_mp3("initial message en.mp3")
                    elif language == 'fr':
                        initial_message_audio = AudioSegment.from_mp3("initial message fr.mp3")
                        
                    initial_message_audio = initial_message_audio.set_frame_rate(8000).set_channels(1)
                    raw_audio_data = initial_message_audio.raw_data
                    mu_law_audio_data = audioop.lin2ulaw(raw_audio_data, 2)  # 2 bytes per sample (16 bits)
                    chunk_size = 160  # 20ms of audio

                    for i in range(0, len(mu_law_audio_data), chunk_size):
                        chunk = mu_law_audio_data[i:i+chunk_size]
                        base64_chunk = base64.b64encode(chunk).decode('utf-8')
                        media_message = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": base64_chunk
                            }
                        }
                        await websocket.send_json(media_message)
                except Exception as e:
                    logger.error(f"Error sending initial message audio: {e}", exc_info=True)
            
            try:
                async for message in websocket.iter_text(): 
                    data = json.loads(message)

                    if data['event'] == 'media' and openai_ws.open:
                        # Pre-process audio before sending
                        raw_audio = data['media']['payload']
                        # raw_audio = preprocess_audio(raw_audio)
                        
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": raw_audio
                        }
                        await openai_ws.send(json.dumps(audio_append))


                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        logger.info(f"Incoming stream has started {stream_sid}")
                        await send_initial_message_audio()

                    elif data['event'] == 'stop':
                        # Extract summary after call ends
                        if VERBOSE:
                            logger.info(f"Customer Ending Twilio call...")


                        # logger.info(f'Full transcript: {transcript}')
                        end_timer = time.time()
                        order_info = await process_transcript_and_send(transcript, end_timer - start_timer, restaurant_id, menu_content, client_number, call_sid, forward, language)
                        
                        # if order not confirmed
                        if not order_info: 
                            return 

                        # Send message to customer
                        twilio_number = get_twilio_number_by_restaurant_id(restaurant_id)
                        client_message = await format_client_message(order_info, forward_phone_number, language)
                        await send_sms_from_twilio(client_number, twilio_number, client_message)

            except WebSocketDisconnect:
                logger.info("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

            except Exception as e:
                logger.error(f"Error in receive_from_twilio: {e}", exc_info=True)
                logger.info(f'\n{"-" * 75}\n')  # Logger separator
                

            finally:
                # Ensure OpenAI WebSocket is closed if still open
                logger.info("Client Disconnected in finally block")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            nonlocal stream_sid, transcript, forward, language
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)

                    if response['type'] in LOG_EVENT_TYPES and VERBOSE:
                        logger.info(f"Received event ({response['type']}): {response}")

                    if response['type'] == 'conversation.item.input_audio_transcription.completed':
                        user_message = response.get('transcript', '').strip()
                        transcript += f"User: {user_message}\n\n"
                        
                        if VERBOSE_TRANSCRIPT:
                            logger.info(f"User: {user_message}\n")

                    if response['type'] == 'response.done':
                        outputs = response.get('response', {}).get('output', [{}])

                        if outputs:
                            agent_message = outputs[0].get('content', [{}])[0].get('transcript', '').strip()
                            
                        else:
                            agent_message = 'Agent message not found'
                            
                        transcript += f"Agent: {agent_message}\n\n"
                        
                        if VERBOSE_TRANSCRIPT:
                            logger.info(f"Agent: {agent_message}\n")

                    if response['type'] == 'session.updated' and VERBOSE:
                        logger.info(f'Session updated successfully: {response}')

                    # Handle 'speech_started' event
                    if response['type'] == 'input_audio_buffer.speech_started':
                        if VERBOSE:
                            logger.info(f"Speech Start: {response['type']}")

                        # Clear ongoing speech on Twilio side
                        clear_event = {
                            "streamSid": stream_sid,
                            "event": "clear"
                        }
                        await websocket.send_json(clear_event)
                        if VERBOSE: 
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
                            logger.info(f'\n{"-" * 75}\n')  # Logger separator

                    # custom function calling
                    if response['type'] == 'conversation.item.created' and response['item']['type'] == 'function_call':

                        # our end twilio call function
                        if response['item']['name'] == 'end_twilio_call':
                            await end_twilio_call(call_sid)

                            if openai_ws.open:
                                await openai_ws.close()
                            return
                        
                        if response['item']['name'] == 'forward_to_live_agent':
                            forward = True
                            await forward_to_live_agent(call_sid, forward_phone_number)

                            # Close WebSocket connection gracefully
                            if openai_ws.open:
                                await openai_ws.close()
                            return

            except Exception as e:
                logger.error(f"Error in send_to_twilio: {e}", exc_info=True)
            
            finally:
                # Ensure OpenAI WebSocket is closed if still open
                if openai_ws.open:
                    await openai_ws.close()

        await asyncio.gather(receive_from_twilio(), send_to_twilio())


async def send_session_update(openai_ws, system_message, VERBOSE=False):
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.8, # [0, 1,0] higher threshold will required louder audio to activate model
                    "prefix_padding_ms": 500, # duration of audio to send before speech start (ms)
                    "silence_duration_ms": 500 # duration of silence to detect speech stop (ms)
                },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": system_message,
            "modalities": ["text", "audio"],
            "temperature": MODEL_TEMPERATURE,
            "input_audio_transcription": {
                "model": "whisper-1"
            },
            "max_response_output_tokens": 1500, # max num of tokens for a single assistant response (including tool calls)
            "tools": [
                {
                    "type": "function",
                    "name": "end_twilio_call",
                    "description": "Ends the call if the conversation has concluded.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "call_sid": {"type": "string"}
                        }
                    }
                },
                {
                    "type": "function",
                    "name": "forward_to_live_agent",
                    "description": "Forwards the call to a live agent at a specified phone number if the user requests so. The user may also refer them to real person, real human, etc.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "call_sid": {"type": "string"},
                            "forward_to": {"type": "string"}
                        },
                        "required": ["call_sid", "forward_to"]
                    }
                }
            ]
        }
    }

    if VERBOSE:
        logger.info(f'Sending session update: {json.dumps(session_update)}')

    await openai_ws.send(json.dumps(session_update))



async def content_extraction(transcript, timer, restaurant_id, menu_content, call_sid, language):
    """Make a ChatGPT API call and enforce schema using JSON."""
    # logger.info("Starting Content Extraction...")

    restaurant_name = get_restaurant_name_by_restaurant_id(restaurant_id)
    montreal_tz = pytz.timezone('America/Montreal')
    current_time = datetime.datetime.now(montreal_tz).strftime('%Y-%m-%d %H:%M:%S')
    full_language = 'English' if language == 'en' else 'French'

    if not transcript.strip():
        return {
            "name": "",
            "phone_number": "",
            "pickup": False,
            "pickup_or_delivery_time": "",
            "confirmation": False,
            "order_info": {
                "items": [],
                "order_id": "",
                "timestamp": current_time,
                "call_sid": call_sid,
                "restaurant_id": restaurant_id,
                "restaurant_name": "",
                "customer_name": "",
                "phone_number": "",
                "pickup": True,
                "pickup_or_delivery_time": "",
            },
            "call_sid": call_sid,
            "timestamp": current_time,
            "timer": timer,
            "transcript": transcript
        }

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    # Request payload with enforced JSON schema
    payload = {
        # "model": "gpt-3.5-turbo",
        "model": "gpt-4o",
        "messages": [
            {
                "role": "system",
                "content": f"""
                Extract the following details from the transcript below in a structured JSON:
                1. name
                2. phone number
                3. order type (always pickup)
                4. pickup or delivery time
                5. order information (item name, quantity, unit price, notes). For each item, make sure to extract any relevant 'notes' from the transcript. If no notes are provided, leave it as an empty string.
                
                All information must be extracted from the given transcript, in {full_language}, and pay close attention to the following details:
                1. If any information is missing, such as the name, pickup time, or anything else, you must leave it empty. NEVER EVER MAKE UP ANY INFORMATION. 
                2. Note that the transcript is a real-time conversation between a customer and the AI, so extract the information as accurately as possible. Be especially careful with the name of the customer. 
                3. Be careful and accurate for combo orders. For example, if the customer orders a combo meal or a party pack, make sure that this is extracted correctly in the order information. One of the worst thing you can do is to charge the client for many items individually rather than the discounted combo price. 
                
                Finally, determine if the order was placed, or if it was a mis-dial, or if the user hung up before finishing and confirming the order. Store this in a 'confirmation' key (as a boolean) if the order seems to have been placed by the user.
                
                For reference, the menu content is provided below:
                {menu_content}
                """
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
                        "confirmation": {"type": "boolean"},
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
                # logger.info(f"ChatGPT API response status: {response.status}")
                data = await response.json()
                # logger.info(f"Full ChatGPT API response: {json.dumps(data, indent=2)}")

                logger.info(f'data: {json.dumps(data, indent=2)}')
                arguments = json.loads(data["choices"][0]["message"]["function_call"]["arguments"])

                # Enrich the response with generated values
                arguments["call_sid"] = call_sid
                arguments["timestamp"] = current_time
                arguments["timer"] = timer
                arguments['transcript'] = transcript #transcript with newlines
                arguments['pickup'] = True # only pickup for now

                # Add formatted order_id to order_info + required info for the order
                timestamp_seconds = int(datetime.datetime.now().timestamp())
                arguments["order_info"]["order_id"] = f"ORD-{timestamp_seconds}-{call_sid[-5:]}"
                arguments['order_info']['timestamp'] = current_time
                arguments['order_info']['call_sid'] = call_sid
                arguments['order_info']['restaurant_id'] = restaurant_id
                arguments['order_info']['restaurant_name'] = restaurant_name
                arguments['order_info']['customer_name'] = arguments['name']
                arguments['order_info']['phone_number'] = arguments['phone_number']
                arguments['order_info']['pickup'] = arguments['pickup']
                arguments['order_info']['pickup_or_delivery_time'] = arguments['pickup_or_delivery_time']

                return arguments

        except Exception as error:
            logger.error(f"Error making ChatGPT completion call: {error}", exc_info=True)
            logger.info(f'\n{"-" * 75}\n')  # Logger separator
            
            raise


async def process_transcript_and_send(transcript, timer, 
                                      restaurant_id, menu_content, client_number, call_sid, forward, language):
    """Process the transcript and send the extracted data to the webhook."""
    try:
        # Make the ChatGPT completion call
        result = await content_extraction(transcript, timer, restaurant_id, menu_content, call_sid, language)

        # Check if the response contains the expected data
        if result:
            logger.info(f'Full result for call id ({result['call_sid']}): {json.dumps(result, indent=2)}')

            # Database connection setup
            connection = create_connection()

            # Insert call record
            call_sid = result["call_sid"]
            restaurant_id = restaurant_id
            transcript_text = result["transcript"]
            customer_phone_number = result["phone_number"]
            customer_name = result["name"]

            # make sure confirmation is False if call is forwarded
            if forward: 
                confirmation = False
            else:
                confirmation = result.get("confirmation", False)

            timestamp = result["timestamp"]
            order_info = result["order_info"]
            insert_call_record(connection, call_sid, restaurant_id, 
                               transcript_text, timestamp, timer, 
                               customer_phone_number, customer_name, 
                               confirmation, forward)

            # Insert order record if confirmed
            if confirmation:
                insert_order_record(connection, order_info)
                insert_order_items(connection, order_info["items"], 
                                   order_info["order_id"])

            # Close database connection
            close_connection(connection)
            
            if not confirmation:
                logger.info(f'{'-' * 25} CALL ID ({call_sid}) NOT CONFIRMED {"-" * 25}')
                return  # Stop if the order was not confirmed

            try:
                # generate pdf receipt and payload
                pdf_base64, receipt_width, receipt_height = generate_pdf_receipt(order_info)

                # save to local folder
                pdf_data = base64.b64decode(pdf_base64)
                with open('receipt.pdf', "wb") as f:
                    f.write(pdf_data)

                payload = create_json_payload(document_base64=pdf_base64, 
                                            document_type='pdf',
                                            ticket_id=call_sid,
                                            paper_width_mm=receipt_width,
                                            paper_height_mm=receipt_height,)

                # logger.info(f'Payload: {json.dumps(payload, indent=2)}')

                # retrieve printer topic id
                printer_topic_id = get_printer_topic_id_by_restaurant_id(str(restaurant_id))
                if not printer_topic_id:
                    raise HTTPException(status_code=404, detail="Printer topic id not found for this restaurant")
                
                # publish the order to the printer
                mqtt_client = connect_mqtt()
                mqtt_client.publish(printer_topic_id, payload)
                mqtt_client.disconnect()
            
            except Exception as e:
                logger.error(f"Error sending order for call id ({call_sid}) to printer: {e}")
                logger.info(f'\n{"-" * 75}\n')  # Logger separator


            return order_info
            
            # # Send order info to Lambda or other processes if needed
            # await send_order_to_lambda(result["order_info"])

    except asyncio.TimeoutError:
        logger.warning("Timed out while processing the transcript.")
    except Exception as error:
        logger.error(f"Error in process_transcript_and_send: {error}")
        logger.info(f'\n{"-" * 75}\n')  # Logger separator
        


# async def send_order_to_lambda(order_info):
#     """Asynchronously send order information to AWS Lambda via webhook and handle PDF response."""
#     lambda_url = os.getenv("AWS_LAMBDA_URL")  # Get the Lambda URL from environment variables

#     headers = {
#         "Content-Type": "application/json"
#     }

#     async with aiohttp.ClientSession() as session:
#         try:
#             logger.info("Sending order info to AWS Lambda...")
#             async with session.post(lambda_url, headers=headers, json=order_info) as response:
                
#                 # If the response is successful (status code 200)
#                 if response.status == 200:
#                     content_type = response.headers.get("Content-Type")
                    
#                     # If the response is a PDF (application/pdf)
#                     if content_type == "application/pdf":
#                         # Read the PDF content as bytes
#                         pdf_content = await response.read()

#                         # Save the PDF to a local file
#                         with open("receipt.pdf", "wb") as pdf_file:
#                             pdf_file.write(pdf_content)

#                         # logger.info("PDF receipt successfully received and saved as 'receipt.pdf'.")
#                         logger.info(f'\n{"-" * 75}\n')  # Logger separator
#                     else:
#                         logger.warning(f"Unexpected content type: {content_type}")
                
#                 else:
#                     logger.warning(f"Failed to send order. Status Code: {response.status}")
#                     text_response = await response.text()
#                     logger.warning(f"Response: {text_response}")

#         except aiohttp.ClientError as e:
#             logger.error(f"Error sending order to Lambda: {e}")
#             logger.info(f'\n{"-" * 75}\n')  # Logger separator
            

async def send_sms_from_twilio(to, from_, body):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    message = client.messages.create(
        to=to,          # Recipient's phone number
        from_=from_,    # Your Twilio phone number
        body=body       # Message content
    )
    logger.info(f'Sending message to {to}')
    return message

async def calculate_wait_time():
    # current time in montreal
    montreal_tz = pytz.timezone('America/Montreal')
    current_time = datetime.datetime.now(montreal_tz)
    
    # hour of the day
    hour = current_time.hour
    
    # morning rush
    if 11 <= hour <= 13:
        return 30

    # afternoon calm
    elif 13 <= hour <= 16:
        return 20
    
    # evening rush
    elif 16 <= hour <= 19:
        return 35
    
    else: # rest of the hours
        return 20
    

async def format_client_message(order_info, forward_phone_number, language = 'en'):
    # Extract order details
    order_id = order_info["order_id"]
    timestamp = order_info["timestamp"]
    restaurant_name = order_info["restaurant_name"]
    customer_name = order_info["customer_name"]
    pickup = order_info["pickup"]
    pickup_or_delivery_time = order_info["pickup_or_delivery_time"]
    
    # Format the order items
    items = order_info["items"]
    items_details = ''
    for item in items:
        items_details += f"- {item['quantity']} x {item['name']} @ ${item['unit_price']:.2f} each"
        
        # check for notes
        notes = item.get("notes", "")
        if notes:
            items_details += f" ({notes})"
        
        items_details += "\n"
    
    # Determine pickup or delivery text
    order_type = "Pickup" if pickup else "Delivery"
    
    # Format the message based on the language
    if language == 'fr':
        message = (
            f"Bonjour {customer_name},\n\n"
            f"Merci pour votre commande chez {restaurant_name} !\n"
            f"ID de la commande : {order_id}\n"
            f"Heure de la commande : {timestamp}\n\n"
            f"Articles :\n{items_details}\n\n"
            f"Heure de {'ramassage' if pickup else 'livraison'} : {pickup_or_delivery_time}\n\n"
            f"Pour toute question, appelez-nous au {forward_phone_number}.\n"
            f"Merci d'avoir choisi {restaurant_name}!"
        )
    else:  # Default to English
        message = (
            f"Hello {customer_name},\n\n"
            f"Thank you for your order from {restaurant_name}!\n"
            f"Order ID: {order_id}\n"
            f"Order Time: {timestamp}\n\n"
            f"Items:\n{items_details}\n\n"
            f"{order_type} Time: {pickup_or_delivery_time}\n\n"
            f"For any questions, call us at {forward_phone_number}.\n"
            f"Thank you for choosing {restaurant_name}!"
        )
    
    return message

async def end_twilio_call(call_sid):
    logger.info("AI Assistant Ending Twilio call...")
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    call = client.calls(call_sid).update(status='completed')
    logger.info(f'Call ended by AI: {call.status}')

async def forward_to_live_agent(call_sid: str, forward_to: str):
    """
    Forward the ongoing call to a live agent.
    """
    logger.info(f"Forwarding call SID {call_sid} to live agent at {forward_to}...")
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        call = client.calls(call_sid).update(
            twiml=f"""
            <Response>
                <Dial record="record-from-ringing">
                    {forward_to}
                </Dial>
            </Response>
            """
        )
        logger.info(f"Call forwarded successfully to {forward_to}: {call.status}")
    except Exception as e:
        logger.error(f"Error forwarding call SID {call_sid} to live agent: {e}")
        raise

@app.post("/twilio-recording")
async def twilio_recording(request: Request):
    form_data = await request.form()

    # Convert form data to a dictionary for logging
    form_dict = {key: value for key, value in form_data.items()}
    # logger.info(f"Twilio recording webhook data: {json.dumps(form_dict, indent=2)}")

    # Extract individual parameters
    call_sid = form_dict.get("CallSid")
    recording_url = form_dict.get("RecordingUrl")
    recording_duration = form_dict.get("RecordingDuration")

    if not call_sid or not recording_url or not recording_duration:
        logger.error("Missing required recording metadata in webhook")
        return {"error": "Missing required parameters"}, 400

    # Insert recording metadata into the database
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return {"error": "Database connection failed"}, 500

    try:
        insert_twilio_recording(connection, call_sid, recording_url, int(recording_duration))
        logger.info('Recording inserted successfully')
        return {"message": "Recording processed successfully"}, 200
    except Exception as e:
        logger.error(f"Error processing Twilio recording webhook: {e}")
        return {"error": "Internal server error"}, 500
    finally:
        close_connection(connection)

async def is_websocket_url_valid(url: str) -> bool:
    try:
        async with websockets.connect(url) as websocket:
            # Optional: Send and receive a ping/pong or basic handshake
            await websocket.ping()
            return True
    except WebSocketException as e:
        logger.error(f"WebSocket handshake failed: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Error validating WebSocket URL: {e}", exc_info=True)
        return False
    
async def create_system_message(client_number, wait_time, menu_content, language):

    if language == 'en':
        system_message = f"""
            You are a friendly and experienced waiter at a restaurant taking orders, and you provide accurate information and helpful recommendations. At the beginning of the call, the customer's first response is in response to the initial message: {INITIAL_MESSAGE} (therefore you don't need to greet them again). During the call, if you do not understand the client's question or message or if the message seems to have been cutoff, you should politely ask them to repeat themselves. You should also keep the following points in mind during the conversation with the client:
            1. Keep the conversation more generic, and do not go into specifics unless the client asks for specific information. This will make the conversation flow better. 
            2. If you need to ask the client for information, stick to asking 1 piece of information per request if possible. It's very important to split up the questions to avoid overwhelming the client.
            3. You should behave like an experienced waiter, and ask meaningful follow up questions when necessary. For example, if a client orders a steak, you should ask them about the desired level of doneness. If a client orders a coffee, you should ask them if they want any milk or sugar. If a client orders a salad, you should ask them about the dressing. If a client orders a soft drink, you should ask them which one and if they want ice.
            4. You should avoid giving any prices during the conversation, except it the client explicitly asks for it. However, if they do request the price, you must be extremely careful to provide the correct price by referring to the menu (below). This also applies when they ask for the order total. You must calculate the total accurately, and tell them that this is the subtotal before tax. 
            5. Make sure to carefully listen to the client's messages. If you are unsure, politely ask them to repeat themselves.
            6. It is extremely important to stick to the menu below when giving out recommendations or taking orders. If the client asks for something that is not on the menu, politely inform them that it is not available. More importantly, you should never recommend something that is not on the menu.
            7. If a client asks for the phone call to be forwarded, you should let them know that you will transfer them to a live agent, and for them to hold the line.
            
            At the end of the call, you should repeat the order to the client and confirm the following:
            1. You need to ask for their name, but do not repeat it after. Simply reply with "thank you" or something similar, and continue the conversation. 
            2. The client's phone number in the format below. Note that this is the number they called from: {client_number[2:5]}-{client_number[5:8]}-{client_number[8:]}. You should ask if this is the correct number they would like to be reached at. If the number is 514-123-4567, you should always repeat the number as "five-one-four, one-two-three, four-five-six-seven". It is extremely important to repeat the phone number in this format.
            3. The ordered items, including the quantity and any special notes. 
            4. Confirm with them that the order is for pickup. Currently, we only support pickup orders and cannot do deliveries at this time. 
            5. The corresponding time for pickup. If the client responds with "as soon as possible", "right now", "how long will it take?" or similar questions, you should tell them that it will take approximately {wait_time} minutes. Do not deviate from this minimum preparation time, but they are allowed to come later if they wish.
            6. If a client has already confirmed some of the information above (such as some ordered items), you do not need to repeat it back to them again.
            7. At the end, you should ask if there is anything else you can do for them, or if that's it. If the client says that's it, you should thank them for their order and tell them to have a great day.

            Below is the menu content. Be very careful and accurate when providing information from the menu.\n {menu_content}
        """
        
        return system_message
        

    elif language == 'fr':
        system_message = f"""
            Vous êtes un serveur amical et expérimenté dans un restaurant qui prend les commandes et fournit des informations précises ainsi que des recommandations utiles. Au début de l'appel, la première réponse du client est en réaction au message initial : {INITIAL_MESSAGE_FR} (par conséquent, vous n'avez pas besoin de les saluer à nouveau). Pendant l'appel, si vous ne comprenez pas la question ou le message du client, ou si le message semble avoir été coupé, demandez-lui poliment de se répéter. Vous devez également garder à l'esprit les points suivants lors de la conversation avec le client :
            1. Gardez la conversation générale et ne rentrez pas dans les détails, sauf si le client demande des informations spécifiques. Cela rendra la conversation plus fluide.
            2. Si vous devez poser des questions au client, demandez une seule information à la fois dans la mesure du possible. Il est très important de diviser les questions pour éviter de submerger le client.
            3. Vous devez agir comme un serveur expérimenté et poser des questions pertinentes lorsque cela est nécessaire. Par exemple, si un client commande un steak, demandez-lui la cuisson souhaitée. Si un client commande un café, demandez-lui s'il souhaite du lait ou du sucre. Si un client commande une salade, demandez-lui quel assaisonnement il préfère. Si un client commande une boisson gazeuse, demandez-lui laquelle il souhaite et s'il veut des glaçons.
            4. Évitez de donner des prix pendant la conversation, sauf si le client le demande explicitement.
            5. Écoutez attentivement les messages du client. Si vous n'êtes pas sûr, demandez-lui poliment de se répéter.
            6. Il est extrêmement important de vous en tenir strictement au menu ci-dessous pour vos recommandations ou pour prendre des commandes. Si le client demande quelque chose qui n'est pas sur le menu, informez-le poliment que ce n'est pas disponible. Plus important encore, ne recommandez jamais quelque chose qui n'est pas sur le menu.
            7. Si un client demande à être transféré à un agent en direct, informez-le que vous allez le transférer et demandez-lui de rester en ligne.

            À la fin de l'appel, vous devez répéter la commande au client et confirmer les points suivants :
            1. Vous devez demander leur nom, mais ne le répétez pas après. Répondez simplement par "merci" ou quelque chose de similaire, et continuez la conversation.
            2. Le numéro de téléphone du client dans le format ci-dessous. Notez qu'il s'agit du numéro à partir duquel il a appelé : {client_number[2:5]}-{client_number[5:8]}-{client_number[8:]}. Vous devez lui demander si c'est bien le bon numéro pour le joindre. Si le numéro est 514-123-4567, vous devez le répéter comme suit : "cinq-un-quatre, un-deux-trois, quatre-cinq-six-sept". Il est extrêmement important de répéter le numéro de téléphone de cette manière.
            3. Les articles commandés, y compris la quantité et les notes spéciales.
            4. Confirmez que la commande est pour un ramassage. Actuellement, nous ne proposons que le ramassage et ne faisons pas de livraisons.
            5. Le temps estimé pour le ramassage. Si le client répond par "dès que possible", "tout de suite", "combien de temps cela prendra ?" ou des questions similaires, informez-le que cela prendra environ {wait_time} minutes. Ne déviez pas de ce temps minimum de préparation, mais le client peut venir plus tard s'il le souhaite.
            6. Si le client a déjà confirmé certaines des informations ci-dessus (comme des articles commandés), vous n'avez pas besoin de les répéter.
            7. Enfin, demandez s'il y a autre chose que vous pouvez faire pour lui ou si c'est tout. Si le client répond que c'est tout, remerciez-le pour sa commande et souhaitez-lui une excellente journée.

            Vous trouverez ci-dessous le contenu du menu. Soyez très attentif et précis lorsque vous fournissez des informations issues du menu.\n {menu_content}
        """
        
        return system_message
    
    else:
        raise HTTPException(status_code=404, detail="Language not supported")
        
    
# run app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0")
