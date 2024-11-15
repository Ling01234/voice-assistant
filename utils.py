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