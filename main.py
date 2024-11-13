import os
import pytz
from printer import *
from pdf import *
from s3_handler import fetch_file_from_s3
from database import *
import time
import uuid
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
from redis_handler import *

load_dotenv()

### ENVIRONMENT VARIABLES ###
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL")
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')


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

VOICE = 'alloy'
MODEL_TEMPERATURE = 0.7 # must be [0.6, 1.2]
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
async def handle_incoming_call(event: dict):
    # Extract the body from the forwarded Lambda event
    body = event.get("body", "")
    is_base64_encoded = event.get("isBase64Encoded", False)

    # Decode the Base64-encoded body if necessary
    if is_base64_encoded:
        decoded_bytes = base64.b64decode(body)
        decoded_body = decoded_bytes.decode('utf-8')
    else:
        decoded_body = body

    # Parse the decoded body as URL-encoded data
    parsed_data = parse_qs(decoded_body)
    # logging.info(f'Incoming call parsed data: {json.dumps(parsed_data, indent=2)}')

    # Extract the To and From number, handling list structure
    twilio_number = parsed_data.get("To", [None])[0]  # Use [0] to get the first element in the list
    client_number = parsed_data.get("From", [None])[0]  # Use [0] to get the first element in the list

    if not twilio_number:
        raise HTTPException(status_code=400, detail="Missing 'To' parameter in the request")

    # Look up the restaurant_id based on the Twilio number
    restaurant_id = get_restaurant_id_by_twilio_number(twilio_number)
    if not restaurant_id:
        raise HTTPException(status_code=404, detail="Restaurant not found for this number")

    # max concurrent calls
    max_concurrent_calls = get_max_concurrent_calls_by_restaurant_id(restaurant_id)
    if not max_concurrent_calls:
        raise HTTPException(status_code=404, detail="Max concurrent calls not found for this restaurant")

    logger.info(f"Restaurant number: {twilio_number}")
    logger.info(f"Restaurant id: {restaurant_id}")
    # logger.info(f"Max concurrent calls: {max_concurrent_calls}")

    # Check the current live call count for this restaurant
    live_calls = await get_live_calls(restaurant_id)
    if live_calls >= max_concurrent_calls:
        response = VoiceResponse()
        response.say("Sorry, all our lines are currently busy. Please try again later.")
        return HTMLResponse(content=str(response), media_type="application/xml")

    # Increment the live call count for this restaurant
    await increment_live_calls(restaurant_id)

    # Allow the call and respond with a greeting
    response = VoiceResponse()
    response.say("Hi, how can I help you today?")
    connect = Connect()
    connect.stream(url=f'wss://angelsbot.net/media-stream/{restaurant_id}/{client_number}')
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

@app.websocket("/media-stream/{restaurant_id}/{client_number}")
async def handle_media_stream(websocket: WebSocket, restaurant_id: int, 
                              client_number: str, verbose=False, transcript_verbose = False):
    logger.info(f"{client_number} connected to media stream for restaurant_id: {restaurant_id}")

    # menu path 
    menu_file_path = get_menu_file_path_by_restaurant_id(str(restaurant_id))
    logger.info(f'Menu file path: {menu_file_path}')
    if not menu_file_path:
        raise HTTPException(status_code=404, detail="Menu file path not found for this restaurant")

    # Fetch the menu content from S3 using the new s3_handler
    try:
        menu_content = fetch_file_from_s3(menu_file_path)
        system_message = f"""
        You are a friendly waiter at a restaurant taking orders, and you provide accurate information and helpful recommendations. During the call, if you do not understand the client's question or message or if the message seems to have been cutoff, you should politely ask them to repeat themselves. At the end, you should repeat the order to the client and confirm the following:
        1. The client's name
        2. The client's phone number (Note that this is the number they called from, so you should ask if this is the correct number they would like to be reached at: {client_number[2:]})
        3. The total price before tax
        4. Whether the order is going to be picked up or delivered
        5. The corresponding time
        
        Keep these notes in mind during the conversation with the client:
        1. If you need to ask the client for information, do not ask too many at a time. Stick to asking 1 to 2 pieces of information per request.
        2. If a client has already confirmed some of the information above, you do not need to repeat it back to them again.
        3. You should behave like an experienced waiter, and ask meaningful follow up questions when necessary. For example, if a client orders a steak, you should ask them about the desired level of doneness. If a client orders a coffee, you should ask them if they want any milk or sugar. 
        4. Make sure to carefully listen to the client's messages, such as when they give you their name. If you are unsure, politely ask them to repeat themselves.

        Below are the extracted content from the menu. Be very careful and accurate when providing information from the menu.\n {menu_content}
        """
    except Exception as e:
        logger.error(f"Failed to retrieve menu: {e}")
        logger.info(f'\n{"-" * 75}\n')  # Logger separator
        raise HTTPException(status_code=500, detail="Failed to retrieve menu from S3")

    start_timer = time.time()
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await send_session_update(openai_ws, system_message)

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
                        # Decrement live call count when the call stops
                        await decrement_live_calls(restaurant_id)

                        # Extract summary after call ends
                        logger.info("Call ended. Extracting customer details...")
                        # logger.info(f'Full transcript: {transcript}')
                        end_timer = time.time()
                        order_info = await process_transcript_and_send(transcript, end_timer - start_timer, restaurant_id, menu_content, client_number)

                        # Send message to customer
                        twilio_number = get_twilio_number_by_restaurant_id(restaurant_id)
                        client_message = await format_client_message(order_info, twilio_number)
                        message = await send_sms_from_twilio(client_number, twilio_number, client_message)

            except WebSocketDisconnect:
                logger.info("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

            except Exception as e:
                logger.error(f"Error in receive_from_twilio: {e}")
                logger.info(f'\n{"-" * 75}\n')  # Logger separator
                

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
                        
                        if transcript_verbose:
                            logger.info(f"User: {user_message}\n")

                    if response['type'] == 'response.done':
                        outputs = response.get('response', {}).get('output', [{}])

                        if outputs:
                            agent_message = outputs[0].get('content', [{}])[0].get('transcript', '').strip()
                            
                        else:
                            agent_message = 'Agent message not found'
                            
                        transcript += f"Agent: {agent_message}\n\n"
                        
                        if transcript_verbose:
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
                        if verbose: 
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
                            

            except Exception as e:
                logger.error(f"Error in send_to_twilio: {e}")
                logger.info(f'\n{"-" * 75}\n')  # Logger separator
                

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_session_update(openai_ws, system_message, verbose=False):
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.7, # higher threshold will required louder audio to activate model
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
            "max_response_output_tokens": 1000 # max num of tokens for a single assistant response (including tool calls)
        }
    }
    
    if verbose:
        logger.info(f'Sending session update: {json.dumps(session_update)}')

    await openai_ws.send(json.dumps(session_update))


async def content_extraction(transcript, timer, restaurant_id, menu_content):
    """Make a ChatGPT API call and enforce schema using JSON."""
    logger.info("Starting Content Extraction...")

    restaurant_name = get_restaurant_name_by_restaurant_id(restaurant_id)
    
    # Generate unique call ID
    call_id = str(uuid.uuid4())
    montreal_tz = pytz.timezone('America/Montreal')
    current_time = datetime.datetime.now(montreal_tz).strftime('%Y-%m-%d %H:%M:%S')

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
                    "Also, note that the transcript is a real time conversation between a customer and the AI, so extract the information as accurately as possible. "
                    "Finally, determine if the order was placed, or if it was an mis-dial, or if the user hung up before finishing and confirming the order. Store this in a 'confirmation' key (as a boolean) if the order seems to have been placed by the user."
                    f"For reference, the menu content is provided below:\n{menu_content}"
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
                # logger.info(f"ChatGPT API response status: {response.status}")
                data = await response.json()
                # logger.info(f"Full ChatGPT API response: {json.dumps(data, indent=2)}")

                # Parse the function call arguments
                arguments = json.loads(data["choices"][0]["message"]["function_call"]["arguments"])

                # Enrich the response with generated values
                arguments["call_id"] = call_id
                arguments["timestamp"] = current_time
                arguments["timer"] = timer
                arguments['transcript'] = transcript #transcript with newlines

                # Add formatted order_id to order_info + required info for the order
                timestamp_seconds = int(datetime.datetime.now().timestamp())
                arguments["order_info"]["order_id"] = f"ORD-{timestamp_seconds}-{call_id[-5:]}"
                arguments['order_info']['timestamp'] = current_time
                arguments['order_info']['call_id'] = call_id
                arguments['order_info']['restaurant_id'] = restaurant_id
                arguments['order_info']['restaurant_name'] = restaurant_name
                arguments['order_info']['customer_name'] = arguments['name']
                arguments['order_info']['phone_number'] = arguments['phone_number']
                arguments['order_info']['pickup'] = arguments['pickup']
                arguments['order_info']['pickup_or_delivery_time'] = arguments['pickup_or_delivery_time']

                return arguments

        except Exception as error:
            logger.error(f"Error making ChatGPT completion call: {error}")
            logger.info(f'\n{"-" * 75}\n')  # Logger separator
            
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
            logger.info(f'\n{"-" * 75}\n')  # Logger separator
            

async def process_transcript_and_send(transcript, timer, 
                                      restaurant_id, menu_content, client_number):
    """Process the transcript and send the extracted data to the webhook."""
    try:
        # Make the ChatGPT completion call
        result = await content_extraction(transcript, timer, restaurant_id, menu_content)

        # Check if the response contains the expected data
        if result:
            logger.info(f'Full result for call id ({result['call_id']}): {json.dumps(result, indent=2)}')

            # Database connection setup
            connection = create_connection()

            # Insert call record
            call_id = result["call_id"]
            restaurant_id = restaurant_id
            transcript_text = result["transcript"]
            confirmation = result.get("confirmation", False)
            timestamp = result["timestamp"]
            order_info = result["order_info"]
            insert_call_record(connection, call_id, restaurant_id, 
                               transcript_text, timestamp, timer, confirmation)

            # Insert order record if confirmed
            if confirmation:
                insert_order_record(connection, order_info)
                insert_order_items(connection, order_info["items"], 
                                   order_info["order_id"])

            # Close database connection
            close_connection(connection)
            
            if not confirmation:
                logger.info(f'{'-' * 25} CALL ID ({call_id}) NOT CONFIRMED {"-" * 25}')
                return  # Stop if the order was not confirmed

            try:
                # generate pdf receipt and payload
                pdf_base64, receipt_width, receipt_height = generate_pdf_receipt(order_info)
                payload = create_json_payload(document_base64=pdf_base64, 
                                            document_type='pdf',
                                            ticket_id=call_id,
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
                logger.error(f"Error sending order for call id ({call_id}) to printer: {e}")
                logger.info(f'\n{"-" * 75}\n')  # Logger separator


            return order_info
            
            # # Send order info to Lambda or other processes if needed
            # await send_order_to_lambda(result["order_info"])

    except asyncio.TimeoutError:
        logger.warning("Timed out while processing the transcript.")
    except Exception as error:
        logger.error(f"Error in process_transcript_and_send: {error}")
        logger.info(f'\n{"-" * 75}\n')  # Logger separator
        


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

                        # logger.info("PDF receipt successfully received and saved as 'receipt.pdf'.")
                        logger.info(f'\n{"-" * 75}\n')  # Logger separator
                    else:
                        logger.warning(f"Unexpected content type: {content_type}")
                
                else:
                    logger.warning(f"Failed to send order. Status Code: {response.status}")
                    text_response = await response.text()
                    logger.warning(f"Response: {text_response}")

        except aiohttp.ClientError as e:
            logger.error(f"Error sending order to Lambda: {e}")
            logger.info(f'\n{"-" * 75}\n')  # Logger separator
            

async def send_sms_from_twilio(to, from_, body):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    message = client.messages.create(
        to=to,          # Recipient's phone number
        from_=from_,    # Your Twilio phone number
        body=body       # Message content
    )
    logger.info(f'Sending message to {to}')
    return message

async def format_client_message(order_info, twilio_numer):
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
    
    # Format the final message
    message = (
        f"Hello {customer_name},\n\n"
        f"Thank you for your order from {restaurant_name}!\n"
        f"Order ID: {order_id}\n"
        f"Order Time: {timestamp}\n\n"
        f"Items:\n{items_details}\n\n"
        f"{order_type} Time: {pickup_or_delivery_time}\n\n"
        f"For any questions, call us at {twilio_numer}.\n"
        f"Thank you for choosing {restaurant_name}!"
    )
    
    return message

# run app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0")
