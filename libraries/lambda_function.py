from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import io
from textwrap import wrap
import json
import base64

def generate_pdf_receipt(event):
    """Generates a professional receipt PDF formatted for an 80mm thermal printer."""
    max_line_width = 25  # Adjust the number of characters per line
    same_item_spacing = 4 * mm  # Spacing between wrapped lines
    between_item_spacing = 8 * mm  # Spacing between items

    # Extract order info
    customer_name = event.get("customer_name", "N/A")
    order_id = event.get("order_id", "N/A")
    timestamp = event.get("timestamp", "N/A")
    items = event.get("items", [])
    subtotal = event.get("subtotal", 0.0)
    tax = event.get("tax", 0.0)
    total = event.get("total", 0.0)

    # PDF page size for 80mm thermal printer
    receipt_width = 80 * mm

    # Calculate the total height for the items section
    item_section_height = 0
    for item in items:
        item_name = item["name"]
        quantity = item["quantity"]
        wrapped_lines = wrap(f"{item_name} x{quantity}", max_line_width)
        item_section_height += len(wrapped_lines) * same_item_spacing

        notes = item.get("notes", "")
        if notes:
            wrapped_notes = wrap(f"Notes: {notes}", max_line_width)
            item_section_height += len(wrapped_notes) * same_item_spacing

        item_section_height += between_item_spacing

    item_section_height -= between_item_spacing  # Remove extra spacing after the last item

    # Calculate the final receipt height dynamically
    header_height = 40 * mm
    total_section_height = 40 * mm
    footer_height = 30 * mm
    margin = 10 * mm

    receipt_height = (
        header_height 
        + item_section_height 
        + total_section_height 
        + footer_height 
        + margin
    )

    # Create an in-memory PDF buffer
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(receipt_width, receipt_height))

    # Set up fonts and title
    pdf.setFont("Courier-Bold", 12)
    pdf.drawCentredString(receipt_width / 2, receipt_height - 10 * mm, "Restaurant Receipt")
    pdf.setFont("Courier", 10)

    # Draw separators
    def draw_separator(y):
        pdf.setFont("Courier", 8)
        pdf.drawString(5 * mm, y, "-" * 40)

    # Customer and order details
    y = receipt_height - 25 * mm
    pdf.drawString(5 * mm, y, f"Order ID: {order_id}")
    y -= 5 * mm
    pdf.drawString(5 * mm, y, f"Customer: {customer_name}")
    y -= 5 * mm
    pdf.drawString(5 * mm, y, f"Date: {timestamp}")

    # Items section
    y -= 15 * mm
    pdf.drawString(5 * mm, y, "Ordered Items:")
    y -= 12 * mm

    for item in items:
        item_name = item["name"]
        quantity = item["quantity"]
        unit_price = item["unit_price"]
        price = f"${unit_price * quantity:.2f}"
        notes = item.get("notes", "")

        wrapped_lines = wrap(f"{item_name} x{quantity}", max_line_width)
        for line in wrapped_lines:
            pdf.drawString(5 * mm, y, line)
            y -= same_item_spacing

        pdf.drawRightString(receipt_width - same_item_spacing, y + same_item_spacing, price)

        if notes:
            pdf.setFont("Courier-Bold", 10)
            wrapped_notes = wrap(f"Notes: {notes}", max_line_width)
            for note_line in wrapped_notes:
                pdf.drawString(5 * mm, y, note_line)
                y -= same_item_spacing
            pdf.setFont("Courier", 10)

        y -= between_item_spacing

    # Totals section
    draw_separator(y - 5 * mm)
    y -= 12 * mm
    pdf.drawString(5 * mm, y, f"Subtotal: ${subtotal:.2f}")
    y -= 7 * mm
    pdf.drawString(5 * mm, y, f"Tax: ${tax:.2f}")
    y -= 7 * mm
    pdf.setFont("Courier-Bold", 12)
    pdf.drawString(5 * mm, y, f"Total: ${total:.2f}")

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

    return buffer.getvalue()

def lambda_handler(event, context):
    """AWS Lambda handler to process order info and generate a PDF receipt."""
    try:
        if "body" in event:
            # Parse the body from a JSON string into a dictionary
            event = json.loads(event["body"])
            
        # Generate the PDF from the order info
        pdf_data = generate_pdf_receipt(event)

        # Return the PDF as a Base64-encoded string (suitable for APIs)
        encoded_pdf = base64.b64encode(pdf_data).decode("latin1")

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/pdf",
                "Content-Disposition": "inline; filename=receipt.pdf"
            },
            "body": encoded_pdf,
            "isBase64Encoded": True
        }

    except Exception as e:
        print(f"Error generating PDF: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Failed to generate PDF", "error": str(e)})
        }