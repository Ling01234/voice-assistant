import json
import os
import base64
from urllib import parse
from twilio.request_validator import RequestValidator
import requests

def validate_twilio_request(url, body, signature):
    """Validates that incoming requests genuinely originated from Twilio."""
    # Initialize the Twilio Request Validator
    auth_token = os.getenv('AUTH_TOKEN')
    validator = RequestValidator(auth_token)

    # Perform the validation
    return validator.validate(url, body, signature)

def lambda_handler(event, context):
    print(f'event: {event}')

    # Extract headers from the API Gateway event
    headers = event.get('headers', {})
    signature = headers.get('x-twilio-signature', '')
    host = headers.get('host', '')
    raw_path = event.get('rawPath', '')

    # Build the full request URL
    query_string = event.get('rawQueryString', '')
    url = f"https://{host}{raw_path}"
    if query_string:
        url += f"?{query_string}"

    # Decode the Base64-encoded body if necessary
    body = event.get('body', '')
    if event.get('isBase64Encoded', False):
        body = base64.b64decode(body).decode('utf-8')

    # Correctly decode the form-encoded data
    body = body.replace("+", " ")
    decoded_body = parse.unquote(body)

    # Parse the form data into a dictionary
    form_data = decoded_body.split("&")
    parsed_body = {key: value for key, value in (pair.split("=") for pair in form_data)}

    # Validate the Twilio request
    if not validate_twilio_request(url, parsed_body, signature):
        print(f'Validation Invalid')
        return {
            'statusCode': 403,
            'body': 'Forbidden - Invalid Twilio request'
        }

    print(f'Validation Successful')

    # Forward the full event to the FastAPI app on EC2
    ec2_response = requests.post(
        # "http://172.31.86.162:8000/incoming-message",  # private ip address
        "http://18.234.100.32:8000/incoming-call",  # public ip address
        json=event,  # Send the full event object
        headers={'Content-Type': 'application/json'}
    )

    return {
        'statusCode': ec2_response.status_code,
        'body': ec2_response.text
    }
