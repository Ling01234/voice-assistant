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

def create_connection():
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected():
            logger.info("Connected to the database")
        return connection
    except Error as e:
        logger.error(f"Error connecting to database: {e}")
        return None

def close_connection(connection):
    if connection.is_connected():
        connection.close()
        logger.info("Database connection closed")

def insert_call_record(connection, call_id, restaurant_id, transcript, 
                       timestamp, confirmation):
    try:
        cursor = connection.cursor()
        query = """
        INSERT INTO Calls (call_id, restaurant_id, transcript, timestamp, confirmation)
        VALUES (%s, %s, %s, %s, %s)
        """
        cursor.execute(query, (call_id, restaurant_id, transcript, 
                               timestamp, confirmation))
        connection.commit()
        logger.info("Call record inserted successfully.")
    except Error as e:
        logger.error(f"Error inserting call record: {e}")

def insert_order_record(connection, order_info):
    try:
        # logger.debug(f"order_id: {order_info.get('order_id')}")
        # logger.debug(f"call_id: {order_info.get('call_id')}")
        # logger.debug(f"restaurant_id: {order_info.get('restaurant_id')}")
        # logger.debug(f"customer_name: {order_info.get('customer_name')}")
        # logger.debug(f"phone_number: {order_info.get('phone_number')}")
        # logger.debug(f"pickup: {order_info.get('pickup')}")
        # logger.debug(f"pickup_or_delivery_time: {order_info.get('pickup_or_delivery_time')}")


        cursor = connection.cursor()
        query = """
        INSERT INTO Orders (order_id, call_id, restaurant_id, customer_name, phone_number, pickup, pickup_or_delivery_time, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            order_info["order_id"], 
            order_info["call_id"], 
            order_info["restaurant_id"], 
            order_info["customer_name"],
            order_info["phone_number"],
            order_info["pickup"],
            order_info["pickup_or_delivery_time"],
            order_info["timestamp"]
        ))
        connection.commit()
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
        logger.info("Order items inserted successfully.")
    
    # error
    except Error as e:
        logger.error(f"Error inserting order items: {e}")

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

def get_menu_file_path_by_restaurant_id(restaurant_id):
    connection = create_connection()
    if not connection:
        logger.error("Failed to connect to the database")
        return None

    try:
        cursor = connection.cursor()
        query = """
        SELECT menu_file_path FROM Restaurants WHERE restaurant_id = %s
        """
        cursor.execute(query, (restaurant_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Error as e:
        logger.error(f"Error retrieving menu_file_path for restaurant_id {restaurant_id}: {e}")
        return None
    finally:
        if connection and connection.is_connected():
            connection.close()