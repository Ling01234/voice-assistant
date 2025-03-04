import base64
import json
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os
import logging
import uuid

# Set up the logger
logger = logging.getLogger("voice-assistant-app")

# Load environment variables from .env file
load_dotenv()

# Retrieve MQTT configuration from environment variables
BROKER_IP = os.getenv("MQTT_BROKER_IP")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT"))
BROKER_USERNAME = os.getenv("MQTT_USERNAME")
BROKER_PASSWORD = os.getenv("MQTT_PASSWORD")
BROKER_CLIENT_ID = os.getenv("MQTT_CLIENT_ID")
BROKER_QOS = int(os.getenv("MQTT_QOS"))


def pdf_to_base64(file_path):
    with open(file_path, "rb") as pdf_file:
        base64_encoded_data = base64.b64encode(pdf_file.read()).decode("utf-8")
    return base64_encoded_data

def create_json_payload(
    document_base64,
    document_type,
    ticket_id,
    paper_type=0,
    paper_width_mm=0,
    paper_height_mm=0,
    cut_paper=1,
    no_tail_feed=0,
    open_drawer1=0,
    open_drawer2=0
):
    """
    Creates a JSON payload for printing specifications.

    :param document_base64: Base64 encoded document data.
    :param document_type: Type of document (PDF, PNG, BMP).
    :param ticket_id: Unique identifier for each ticket. Duplicate IDs will prevent printing.
    :param paper_type: Type of paper.
        0: Determined by the printer
        1: Continuous paper
        2: Label paper
        Default: 0
    :param paper_width_mm: Width of the paper in mm. Default is 0 (printer-defined width).
    :param paper_height_mm: Height of the paper in mm. Default is 0 (no limit on page height).
    :param cut_paper: Specifies paper cutting behavior.
        0: No cut
        1: Cut after each page
        2: Cut at the end of the document
        Default: 1
    :param no_tail_feed: Controls whether the paper is fed after printing.
        0: Feed to tear-off position
        1: No feed after printing
        Default: 0
    :param open_drawer1: Opens cash drawer 1 if set to 1. Default: 0
    :param open_drawer2: Opens cash drawer 2 if set to 1. Default: 0
    :return: JSON string of the payload.
    """
    payload = {
        "data_base64": document_base64,
        "data_type": document_type,
        "ticket_id": ticket_id,
        "paper_type": paper_type,
        "paper_width_mm": paper_width_mm,
        "paper_height_mm": paper_height_mm,
        "cut_paper": cut_paper,
        "serial_paper_img_print_no_tail_feed": no_tail_feed,
        "kick_drawer1": open_drawer1,
        "kick_drawer2": open_drawer2
    }
    return json.dumps(payload)



# connected the MQTT SERVER
def connect_mqtt():
    def on_connect(client, userdata, flags, rc):
        if rc==0:
            logger.info("Connected to MQTT Broker!")
        else:
            logger.info("Failed to connect to MQTT,Return code %d\n ",rc)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=BROKER_CLIENT_ID)
    client.on_connect = on_connect
    client.username_pw_set(BROKER_USERNAME,BROKER_PASSWORD)
    client.connect(BROKER_IP,BROKER_PORT,BROKER_QOS)
    # logger.info(f'Connecting to MQTT broker at {BROKER_IP}:{BROKER_PORT}')
    return client

#publsh the manager to printer
if __name__ == "__main__":
    topic = os.getenv("MQTT_TEST_TOPIC_ID")
    ticket_id = str(uuid.uuid4())
    client = connect_mqtt()

    file_path = 'receipt.pdf'
    data_base64 = pdf_to_base64(file_path)
    payload = create_json_payload(data_base64, 'pdf', 
                                  ticket_id=ticket_id)

    logger.info(f'\npayload: {json.dumps(payload, indent=2)}')
    client.publish(topic, payload)
    client.disconnect()


