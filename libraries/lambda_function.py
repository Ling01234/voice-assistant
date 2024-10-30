from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import io
from textwrap import wrap
import json
import base64

TAX_RATE = 0.15

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

    # subtotal
    subtotal = 0
    for item in items:
        item_price = item['quantity'] * item['unit_price']
        subtotal += item_price

    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + tax, 2) 

    # PDF page size for 80mm thermal printer
    receipt_width = 80 * mm  # 80mm width

    # Calculate total height for the items section
    item_section_height = 0  # Initialize total height for items

    for item in items:
        item_name = item["name"]
        quantity = item["quantity"]

        # Wrap item_name to fit within the receipt width
        wrapped_lines = wrap(f"{item_name} x{quantity}", max_line_width)

        # Add height for all lines of the current item
        item_section_height += len(wrapped_lines) * same_item_spacing

        # Check if notes exist to adjust item height
        notes = item.get("notes", "")
        if notes:
            wrapped_notes = wrap(notes, max_line_width)
            item_section_height += len(wrapped_notes) * same_item_spacing

        # Add spacing between items (only after each full item is drawn)
        item_section_height += between_item_spacing

    # Remove extra spacing after the last item
    item_section_height -= between_item_spacing  # Avoid adding spacing after the last item

    # Calculate the final receipt height dynamically
    header_height = 40 * mm  # Space for title and order details
    total_section_height = 4 * between_item_spacing  # Subtotal, tax, and total section
    footer_height = 30 * mm  # Footer space for thank you message
    margin = 15 * mm  # Extra margin to avoid clipping

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

    # Set up fonts
    pdf.setFont("Courier-Bold", 12)
    pdf.drawCentredString(receipt_width / 2, receipt_height - 10 * mm, "Restaurant Receipt")

    pdf.setFont("Courier", 10)

    # Draw separators
    def draw_separator(y):
        pdf.setFont("Courier", 8)
        pdf.drawString(5 * mm, y, "-" * 40)  # Dashed separator line

    # Customer and order details
    y = receipt_height - 20 * mm
    pdf.drawString(5 * mm, y, f"Order ID: {order_id}")
    y -= 5 * mm
    pdf.drawString(5 * mm, y, f"Customer: {customer_name}")
    y -= 5 * mm
    pdf.drawString(5 * mm, y, f"Date: {timestamp}")

    # Items section
    y -= 15 * mm
    pdf.drawString(5 * mm, y, "Ordered Items:")
    y -= between_item_spacing

    for item in items:
        item_name = item["name"]
        quantity = item["quantity"]
        unit_price = item["unit_price"]
        price = f"${unit_price * quantity:.2f}"
        notes = item.get("notes", "")

        # Wrap item_name to fit within the receipt width
        wrapped_lines = wrap(f"{item_name} x{quantity}", max_line_width)

        for line in wrapped_lines:
            pdf.drawString(5 * mm, y, line)
            y -= same_item_spacing  # Adjust spacing between wrapped lines

        # Print price aligned to the right, only once per item
        pdf.drawRightString(receipt_width - same_item_spacing, y + same_item_spacing, price)

        # If notes exist, print them on the same line as "Notes:"
        if notes:
            pdf.setFont("Courier-Bold", 10)  # Bold for "Notes:"
            notes_text = f"Notes: {notes}"  # Concatenate "Notes:" with the actual notes
            
            # Wrap the concatenated text to fit within the receipt width
            wrapped_notes = wrap(notes_text, max_line_width)
            
            # Draw the first line of the wrapped notes
            pdf.drawString(5 * mm, y, wrapped_notes[0])

            # If there are more lines, print them below with appropriate spacing
            for note_line in wrapped_notes[1:]:
                y -= same_item_spacing  # Adjust spacing between lines of the note
                pdf.drawString(5 * mm, y, note_line)

            pdf.setFont("Courier", 10)  # Switch back to regular font for the content

            y += same_item_spacing
            y -= between_item_spacing  # Adjust spacing for the next item
        y -= between_item_spacing  # Adjust spacing for the next item

    # Update the last item's height
    y += between_item_spacing

    # Totals section
    draw_separator(y - between_item_spacing)
    y -= 2 * between_item_spacing
    pdf.drawString(5 * mm, y, f"Subtotal:")
    pdf.drawRightString(receipt_width - 5 * mm, y, f"${subtotal:.2f}")

    y -= between_item_spacing
    pdf.drawString(5 * mm, y, f"Tax:")
    pdf.drawRightString(receipt_width - 5 * mm, y, f"${tax:.2f}")

    y -= between_item_spacing
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