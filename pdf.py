from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import io

def generate_pdf_receipt(event):
    """Generates a professional receipt PDF formatted for an 80mm thermal printer."""

    # Extract order info from the event
    customer_name = event.get("customer_name", "N/A")
    order_id = event.get("order_id", "N/A")
    timestamp = event.get("timestamp", "N/A")
    items = event.get("items", [])
    subtotal = event.get("subtotal", 0.0)
    tax = event.get("tax", 0.0)
    total = event.get("total", 0.0)

    # PDF page size for 80mm thermal printer
    receipt_width = 80 * mm  # 80mm width

    # Height calculation with better spacing
    header_height = 40 * mm  # Space for title and order details
    item_section_height = len(items) * 8 * mm  # Each item gets 12mm for spacing
    total_section_height = 40 * mm  # Subtotal, tax, and total section
    footer_height = 30 * mm  # Footer space for thank you message
    margin = 10 * mm  # Extra margin to avoid clipping

    # Calculate the final receipt height dynamically
    receipt_height = header_height + item_section_height + total_section_height + footer_height + margin

    # Create an in-memory PDF buffer
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(receipt_width, receipt_height))

    # Set up fonts
    pdf.setFont("Courier-Bold", 12)
    pdf.drawCentredString(receipt_width / 2, receipt_height - 10 * mm, "Restaurant Receipt")

    pdf.setFont("Courier", 10)

    # Draw separators
    def draw_separator(y):
        pdf.setFont("Courier", 8)
        pdf.drawString(5 * mm, y, "-" * 40)  # Dashed separator line

    # Customer and order details
    y = receipt_height - 25 * mm
    pdf.drawString(5 * mm, y, f"Order ID: {order_id}")
    y -= 5 * mm
    pdf.drawString(5 * mm, y, f"Customer: {customer_name}")
    y -= 5 * mm
    pdf.drawString(5 * mm, y, f"Date: {timestamp}")

    # Items section
    y -= 15 * mm
    pdf.drawString(5 * mm, y, "Items:")
    y -= 12 * mm

    for item in items:
        item_name = item["name"]
        quantity = item["quantity"]
        unit_price = item["unit_price"]
        line = f"{item_name} x{quantity}"
        price = f"${unit_price * quantity:.2f}"
        
        pdf.drawString(5 * mm, y, line)
        pdf.drawRightString(receipt_width - 5 * mm, y, price)
        y -= 8 * mm  # Adjust spacing for the next item

    # Totals section
    draw_separator(y - 5 * mm)
    y -= 12 * mm
    pdf.drawString(5 * mm, y, f"Subtotal:")
    pdf.drawRightString(receipt_width - 5 * mm, y, f"${subtotal:.2f}")

    y -= 7 * mm
    pdf.drawString(5 * mm, y, f"Tax:")
    pdf.drawRightString(receipt_width - 5 * mm, y, f"${tax:.2f}")

    y -= 7 * mm
    pdf.drawString(5 * mm, y, f"Total:")
    pdf.setFont("Courier-Bold", 12)
    pdf.drawRightString(receipt_width - 5 * mm, y, f"${total:.2f}")

    # Footer section
    pdf.setFont("Courier", 8)
    y -= 15 * mm
    pdf.drawCentredString(receipt_width / 2, y, "Thank you for your order!")
    y -= 10 * mm
    pdf.drawCentredString(receipt_width / 2, y, "Visit us again!")

    # Finalize PDF and save to buffer
    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    # Save the buffer content to a local PDF file for testing
    with open("thermal_printer_receipt.pdf", "wb") as f:
        f.write(buffer.read())

    print("PDF receipt generated: thermal_printer_receipt.pdf")

# Example usage with sample order info
order_info = {
    "order_id": "ORD-1698001234-12345",
    "customer_name": "Jane Doe",
    "timestamp": "22-10-2024T08:06:03",
    "items": [
        {"name": "General Tao Chicken", "quantity": 1, "unit_price": 18.00},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
        {"name": "Wonton Soup", "quantity": 1, "unit_price": 4.99},
    ],
    "subtotal": 22.99,
    "tax": 3.45,
    "total": 26.44
}

# Run the function with the example event
generate_pdf_receipt(order_info)
