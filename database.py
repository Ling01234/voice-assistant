import mysql.connector
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
    logger.info("Starting connection to the database...")
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

def insert_call_record(connection, call_id, restaurant_id, transcript, confirmation):
    try:
        cursor = connection.cursor()
        query = """
        INSERT INTO Calls (call_id, restaurant_id, transcript, confirmation)
        VALUES (%s, %s, %s, %s)
        """
        cursor.execute(query, (call_id, restaurant_id, transcript, confirmation))
        connection.commit()
        logger.info("Call record inserted successfully.")
    except Error as e:
        logger.error(f"Error inserting call record: {e}")

def insert_order_record(connection, order_info):
    try:
        cursor = connection.cursor()
        query = """
        INSERT INTO Orders (order_id, call_id, restaurant_id, customer_name, phone_number, pickup, pickup_or_delivery_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            order_info["order_id"], 
            order_info["call_id"], 
            order_info["restaurant_id"], 
            order_info["customer_name"],
            order_info["phone_number"],
            order_info["pickup"],
            order_info["pickup_or_delivery_time"]
        ))
        connection.commit()
        logger.info("Order record inserted successfully.")
    except Error as e:
        logger.error(f"Error inserting order record: {e}")