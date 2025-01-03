import mysql.connector
import json
from mysql.connector import Error
from dotenv import load_dotenv
import os
import logging

# Set up the logger
logger = logging.getLogger("voice-assistant-app")

# Load environment variables from .env file
load_dotenv()

# Database connection configuration
DB_CONFIG = {
    'host': os.getenv("DB_HOST"),
    'database': os.getenv("DB_NAME"),
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'port': os.getenv("DB_PORT", 3306)
}
VERBOSE = False

def create_connection(verbose=False):
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected() and VERBOSE:
            logger.info("Connected to the database")
        return connection
    except Error as e:
        logger.error(f"Error connecting to database: {e}")
        return None

def close_connection(connection):
    if connection.is_connected() and VERBOSE:
        connection.close()
        logger.info("Database connection closed")

def insert_call_record(connection, call_sid, restaurant_id, transcript, 
                       timestamp, timer, customer_phone_number, 
                       customer_name, confirmation, forward):
    try:
        cursor = connection.cursor()
        query = """
        INSERT INTO Calls (call_sid, restaurant_id, transcript, timestamp, timer, customer_phone_number, customer_name, confirmation, call_forwarded)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (call_sid, restaurant_id, transcript, 
                               timestamp, timer, customer_phone_number, 
                               customer_name, confirmation, forward))
        connection.commit()
        
        if VERBOSE:
            logger.info("Call record inserted successfully.")
    except Error as e:
        logger.error(f"Error inserting call record: {e}")

def insert_order_record(connection, order_info):
    try:
        # logger.debug(f"order_id: {order_info.get('order_id')}")
        # logger.debug(f"call_sid: {order_info.get('call_sid')}")
        # logger.debug(f"restaurant_id: {order_info.get('restaurant_id')}")
        # logger.debug(f"customer_name: {order_info.get('customer_name')}")
        # logger.debug(f"phone_number: {order_info.get('phone_number')}")
        # logger.debug(f"pickup: {order_info.get('pickup')}")
        # logger.debug(f"pickup_or_delivery_time: {order_info.get('pickup_or_delivery_time')}")


        cursor = connection.cursor()
        query = """
        INSERT INTO Orders (order_id, call_sid, restaurant_id, customer_name, phone_number, pickup, pickup_or_delivery_time, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            order_info["order_id"], 
            order_info["call_sid"], 
            order_info["restaurant_id"], 
            order_info["customer_name"],
            order_info["phone_number"],
            order_info["pickup"],
            order_info["pickup_or_delivery_time"],
            order_info["timestamp"]
        ))
        connection.commit()
        
        if VERBOSE:
            logger.info("Order record inserted successfully.")
    except Error as e:
        logger.error(f"Error inserting order record: {e}")


def insert_order_items(connection, order_items, order_id):
    """
    Inserts multiple order items into the Order_Items table.

    Parameters:
        connection (MySQLConnection): The database connection.
        order_items (list): A list of dictionaries, each containing item information.
    """
    try:
        cursor = connection.cursor()
        
        query = """
        INSERT INTO Order_Items (order_id, item_name, quantity, unit_price, notes)
        VALUES (%s, %s, %s, %s, %s)
        """
        
        # Iterate through each order item and insert it
        for item in order_items:
            cursor.execute(query, (
                order_id,
                item["name"],
                item["quantity"],
                item["unit_price"],
                item.get("notes", "")  # Use an empty string if notes are not provided
            ))
        
        connection.commit()
        
        if VERBOSE:
            logger.info("Order items inserted successfully.")
    
    # error
    except Error as e:
        logger.error(f"Error inserting order items: {e}")

def insert_twilio_recording(connection, call_sid, recording_url, recording_duration):
    """
    Inserts Twilio recording details into the Calls table.

    Parameters:
        connection (MySQLConnection): The database connection.
        call_sid (str): The Twilio Call SID.
        recording_url (str): The URL of the recording.
        recording_duration (int): The duration of the recording in seconds.
    """
    try:
        cursor = connection.cursor()
        query = """
        UPDATE Calls
        SET recording_url = %s, recording_duration = %s
        WHERE call_sid = %s
        """
        cursor.execute(query, (recording_url, recording_duration, call_sid))
        connection.commit()
        
        if VERBOSE:
            logger.info("Recording metadata inserted successfully.")
    except Error as e:
        logger.error(f"Error inserting recording metadata to mysql: {e}")

def get_restaurant_id_by_twilio_number(twilio_number):
    """
    Retrieves the restaurant_id associated with a given Twilio phone number.

    Parameters:
        twilio_number (str): The Twilio phone number to look up.

    Returns:
        int: The restaurant_id if found, otherwise None.
    """
    connection = None
    try:
        # Create a database connection
        connection = create_connection()
        if not connection:
            logger.error("Failed to connect to the database")
            return None

        # Query for the restaurant_id
        cursor = connection.cursor()
        query = """
        SELECT restaurant_id FROM Restaurants WHERE twilio_number = %s
        """
        cursor.execute(query, (twilio_number,))
        result = cursor.fetchone()

        if result:
            return result[0]
        else:
            logger.info(f"No restaurant found for Twilio number {twilio_number}")
            return None
    except Error as e:
        logger.error(f"Error retrieving restaurant_id for Twilio number {twilio_number}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()

def get_menu_file_path_by_restaurant_id(restaurant_id, language):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None
    
    menu_path = 'menu_file_path_en' if language == 'en' else 'menu_file_path_fr'

    try:
        cursor = connection.cursor()
        query = f"""
        SELECT {menu_path} FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving {menu_path} for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()

def get_max_concurrent_calls_by_restaurant_id(restaurant_id):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None

    try:
        cursor = connection.cursor()
        query = """
        SELECT max_concurrent_calls FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving max_concurrent_calls for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()


def get_printer_topic_id_by_restaurant_id(restaurant_id):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None

    try:
        cursor = connection.cursor()
        query = """
        SELECT printer_topic_id FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving printer_topic_id for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()


def get_restaurant_name_by_restaurant_id(restaurant_id):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None

    try:
        cursor = connection.cursor()
        query = """
        SELECT name FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving name for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()
            
def get_twilio_number_by_restaurant_id(restaurant_id):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None

    try:
        cursor = connection.cursor()
        query = """
        SELECT twilio_number FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving name for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()
            
def get_forward_phone_number_by_restaurant_id(restaurant_id):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None

    try:
        cursor = connection.cursor()
        query = """
        SELECT forward_number FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving name for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()


def get_subscription_status_by_restaurant_id(restaurant_id):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None

    try:
        cursor = connection.cursor()
        query = """
        SELECT subscription FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving subscription status for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()