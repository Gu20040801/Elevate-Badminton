import csv
import json
import os
import re
import smtplib
import time
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Flask, render_template, request

app = Flask(__name__)


def load_local_env():
    env_path = Path(".env")

    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()

BOOKING_INBOX = "elevatebadminton99@gmail.com"
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_FROM = os.environ.get("RESEND_FROM", "Elevate Badminton <onboarding@resend.dev>")
RESEND_TIMEOUT_SECONDS = int(os.environ.get("RESEND_TIMEOUT_SECONDS", "8"))
SMTP_TIMEOUT_SECONDS = int(os.environ.get("SMTP_TIMEOUT_SECONDS", "8"))
BOOKING_RECORDS_PATH = Path(
    os.environ.get("BOOKING_RECORDS_PATH", Path(app.instance_path) / "bookings.csv")
)
BOOKING_RECORD_FIELDS = [
    "booking_id",
    "submitted_at",
    "status",
    "lesson_type",
    "preferred_location",
    "preferred_date",
    "preferred_time",
    "player_level",
    "first_name",
    "last_name",
    "email",
    "phone",
    "message",
    "source",
    "ip_address",
    "user_agent",
]
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LESSON_TYPES = {"Private Lessons", "Sparring"}
TRAINING_LOCATIONS = {"Badminton Vancouver", "CoSports", "Flexible"}
MIN_FORM_SECONDS = 3
RATE_LIMIT_SECONDS = 600
RATE_LIMIT_MAX_SUBMISSIONS = 5
RATE_LIMITS = {}
MAX_FIELD_LENGTHS = {
    "name": 80,
    "first_name": 60,
    "last_name": 60,
    "email": 120,
    "preferred_time": 80,
    "player_level": 80,
    "message": 1200,
}
SHOP_ITEMS = [
    {
        "name": "Kinesiology Tape",
        "price": "$15",
        "summary": "Athletic support tape for training, recovery, and match preparation.",
        "image": "tape.png",
    },
]


def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")

    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    if len(digits) != 10:
        return None

    return f"+1{digits}"


def is_future_booking_date(value):
    try:
        preferred_date = date.fromisoformat(value)
    except ValueError:
        return False

    return preferred_date > date.today()


def client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def rate_limit_key(form_name):
    return f"{form_name}:{client_ip()}"


def is_rate_limited(form_name):
    now = time.time()
    key = rate_limit_key(form_name)
    recent_submissions = [
        submitted_at
        for submitted_at in RATE_LIMITS.get(key, [])
        if now - submitted_at < RATE_LIMIT_SECONDS
    ]

    if len(recent_submissions) >= RATE_LIMIT_MAX_SUBMISSIONS:
        RATE_LIMITS[key] = recent_submissions
        return True

    recent_submissions.append(now)
    RATE_LIMITS[key] = recent_submissions
    return False


def form_was_submitted_too_fast():
    try:
        rendered_at = float(request.form.get("form_rendered_at", "0"))
    except ValueError:
        return True

    return time.time() - rendered_at < MIN_FORM_SECONDS


def field_is_too_long(values):
    return any(
        len(values.get(field, "")) > max_length
        for field, max_length in MAX_FIELD_LENGTHS.items()
    )


def send_email(subject, to_address, reply_to, body):
    resend_api_key = os.environ.get("RESEND_API_KEY")

    if resend_api_key:
        send_resend_email(subject, to_address, reply_to, body, resend_api_key)
        return

    send_smtp_email(subject, to_address, reply_to, body)


def send_resend_email(subject, to_address, reply_to, body, api_key):
    payload = {
        "from": RESEND_FROM,
        "to": [to_address],
        "subject": subject,
        "text": body,
        "reply_to": reply_to,
    }
    request_payload = json.dumps(payload).encode("utf-8")
    email_request = Request(
        RESEND_API_URL,
        data=request_payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "elevate-badminton/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(email_request, timeout=RESEND_TIMEOUT_SECONDS) as response:
            if response.status >= 400:
                raise RuntimeError(f"Resend API returned status {response.status}.")
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend API failed with status {error.code}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"Resend API request failed: {error.reason}") from error


def send_smtp_email(subject, to_address, reply_to, body):
    gmail_user = os.environ.get("GMAIL_USER", BOOKING_INBOX)
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD is not configured.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = gmail_user
    message["To"] = to_address
    message["Reply-To"] = reply_to
    message.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.login(gmail_user, gmail_app_password)
        smtp.send_message(message)


def send_site_message(subject, reply_to, body):
    send_email(subject, BOOKING_INBOX, reply_to, body)


def create_booking_record(inquiry):
    return {
        "booking_id": f"BK-{uuid4().hex[:8].upper()}",
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "new",
        "lesson_type": inquiry["lesson_type"],
        "preferred_location": inquiry["preferred_location"],
        "preferred_date": inquiry["preferred_date"],
        "preferred_time": inquiry["preferred_time"],
        "player_level": inquiry["player_level"],
        "first_name": inquiry["first_name"],
        "last_name": inquiry["last_name"],
        "email": inquiry["email"],
        "phone": inquiry["phone"],
        "message": inquiry["message"],
        "source": "bookings",
        "ip_address": client_ip(),
        "user_agent": request.headers.get("User-Agent", ""),
    }


def save_booking_record(record):
    BOOKING_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = BOOKING_RECORDS_PATH.exists()

    with BOOKING_RECORDS_PATH.open("a", newline="", encoding="utf-8") as booking_file:
        writer = csv.DictWriter(booking_file, fieldnames=BOOKING_RECORD_FIELDS)

        if not file_exists:
            writer.writeheader()

        writer.writerow({field: record.get(field, "") for field in BOOKING_RECORD_FIELDS})


def send_booking_inquiry(inquiry):
    first_name = inquiry["first_name"]
    last_name = inquiry["last_name"] or "Not provided"
    body = "\n".join(
        [
            "A new booking inquiry was submitted.",
            "",
            f"Booking ID: {inquiry['booking_id']}",
            f"Submitted at: {inquiry['submitted_at']}",
            f"Status: {inquiry['status']}",
            "",
            f"Lesson type: {inquiry['lesson_type']}",
            f"Preferred location: {inquiry['preferred_location']}",
            f"Preferred date: {inquiry['preferred_date']}",
            f"Preferred time: {inquiry['preferred_time']}",
            f"Player level: {inquiry['player_level'] or 'Not provided'}",
            "",
            f"First name: {first_name}",
            f"Last name: {last_name}",
            f"Email: {inquiry['email']}",
            f"Phone: {inquiry['phone']}",
            f"IP address: {inquiry['ip_address'] or 'Not available'}",
            "",
            "Message:",
            inquiry["message"],
        ]
    )

    send_site_message(
        f"New booking inquiry from {first_name} {last_name}",
        inquiry["email"],
        body,
    )


def send_booking_confirmation(inquiry):
    first_name = inquiry["first_name"]
    body = "\n".join(
        [
            f"Hi {first_name},",
            "",
            "Thanks for contacting Elevate Badminton Academy. We received your training inquiry and will reply within 24 hours.",
            "",
            f"Booking reference: {inquiry['booking_id']}",
            f"Lesson type: {inquiry['lesson_type']}",
            f"Preferred date/time: {inquiry['preferred_date']} at {inquiry['preferred_time']}",
            f"Preferred location: {inquiry['preferred_location']}",
            "",
            "Your lesson time is not confirmed yet. We will confirm coach availability, court booking, and payment details before reserving the slot.",
            "Cancellations or rescheduling requests should be made at least 72 hours before the lesson time.",
            "",
            "Your message:",
            inquiry["message"],
            "",
            "Elevate Badminton Academy",
        ]
    )

    send_email(
        "We received your Elevate Badminton inquiry",
        inquiry["email"],
        BOOKING_INBOX,
        body,
    )


def send_contact_message(contact_message):
    name = contact_message["name"]
    phone = contact_message["phone"] or "Not provided"
    body = "\n".join(
        [
            "A new contact message was submitted.",
            "",
            f"Name: {name}",
            f"Email: {contact_message['email']}",
            f"Phone: {phone}",
            "",
            "Message:",
            contact_message["message"],
        ]
    )

    send_site_message(
        f"New contact message from {name}",
        contact_message["email"],
        body,
    )


@app.route("/")
def home():
    return render_template("index.html")

@app.route("/programs")
def programs():
    return render_template("programs.html")

@app.route("/bookings", methods=["GET", "POST"])
def bookings():
    booking_values = {}

    if request.method == "POST":
        booking_values = request.form.to_dict()

        # Bots often fill hidden fields that real visitors never see.
        if request.form.get("website"):
            return render_template("bookings.html", booking_status="sent", booking_values={})

        if form_was_submitted_too_fast() or is_rate_limited("bookings"):
            return render_template(
                "bookings.html",
                booking_error="Please wait a moment before submitting again.",
                booking_values=booking_values,
            ), 429

        inquiry = {
            "first_name": request.form.get("first_name", "").strip(),
            "last_name": request.form.get("last_name", "").strip(),
            "email": request.form.get("email", "").strip(),
            "phone": normalize_phone(request.form.get("phone") or request.form.get("phone_display")),
            "lesson_type": request.form.get("lesson_type", "").strip(),
            "preferred_location": request.form.get("preferred_location", "").strip(),
            "preferred_date": request.form.get("preferred_date", "").strip(),
            "preferred_time": request.form.get("preferred_time", "").strip(),
            "player_level": request.form.get("player_level", "").strip(),
            "message": request.form.get("message", "").strip(),
        }

        booking_error = None

        if field_is_too_long(inquiry):
            booking_error = "Please shorten your message and try again."
        elif not inquiry["first_name"]:
            booking_error = "Please enter your first name."
        elif not EMAIL_PATTERN.match(inquiry["email"]):
            booking_error = "Please enter a valid email address."
        elif not inquiry["phone"]:
            booking_error = "Please enter a 10-digit Canadian phone number."
        elif inquiry["lesson_type"] not in LESSON_TYPES:
            booking_error = "Please select a lesson type."
        elif inquiry["preferred_location"] not in TRAINING_LOCATIONS:
            booking_error = "Please select a preferred location."
        elif not inquiry["preferred_date"]:
            booking_error = "Please select a preferred date."
        elif not is_future_booking_date(inquiry["preferred_date"]):
            booking_error = "Please select a preferred date at least one day from today."
        elif not inquiry["preferred_time"]:
            booking_error = "Please enter a preferred time."
        elif len(inquiry["message"]) < 10:
            booking_error = "Please add a little more detail to your message."

        if booking_error:
            return render_template(
                "bookings.html",
                booking_error=booking_error,
                booking_values=booking_values,
            ), 400

        booking_record = create_booking_record(inquiry)

        try:
            save_booking_record(booking_record)
        except Exception:
            app.logger.exception("Booking inquiry record could not be saved.")
            return render_template(
                "bookings.html",
                booking_error="The inquiry could not be sent right now. Please try again shortly.",
                booking_values=booking_values,
            ), 503

        try:
            send_booking_inquiry(booking_record)
        except Exception:
            app.logger.exception("Booking inquiry email failed after record was saved.")
            return render_template(
                "bookings.html",
                booking_error=(
                    "Your inquiry was saved, but the notification email could not be sent. "
                    "Please contact Elevate Badminton directly or try again shortly."
                ),
                booking_values=booking_values,
            ), 503

        try:
            send_booking_confirmation(booking_record)
        except Exception:
            app.logger.exception("Booking confirmation email failed.")

        return render_template(
            "bookings.html",
            booking_status="sent",
            booking_reference=booking_record["booking_id"],
            booking_values={},
        )

    return render_template("bookings.html", booking_values=booking_values)

@app.route("/shop")
def shop():
    return render_template("shop.html", shop_items=SHOP_ITEMS)

@app.route("/locations")
def locations():
    return render_template("locations.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    contact_values = {}

    if request.method == "POST":
        contact_values = request.form.to_dict()

        if request.form.get("website"):
            return render_template("contact.html", contact_status="sent", contact_values={})

        if form_was_submitted_too_fast() or is_rate_limited("contact"):
            return render_template(
                "contact.html",
                contact_error="Please wait a moment before submitting again.",
                contact_values=contact_values,
            ), 429

        contact_message = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip(),
            "phone": normalize_phone(request.form.get("phone") or request.form.get("phone_display")),
            "message": request.form.get("message", "").strip(),
        }

        contact_error = None

        if field_is_too_long(contact_message):
            contact_error = "Please shorten your message and try again."
        elif not contact_message["name"]:
            contact_error = "Please enter your name."
        elif not EMAIL_PATTERN.match(contact_message["email"]):
            contact_error = "Please enter a valid email address."
        elif request.form.get("phone_display") and not contact_message["phone"]:
            contact_error = "Please enter a 10-digit Canadian phone number."
        elif len(contact_message["message"]) < 10:
            contact_error = "Please add a little more detail to your message."

        if contact_error:
            return render_template(
                "contact.html",
                contact_error=contact_error,
                contact_values=contact_values,
            ), 400

        try:
            send_contact_message(contact_message)
        except Exception:
            app.logger.exception("Contact message email failed.")
            return render_template(
                "contact.html",
                contact_error="The message could not be sent right now. Please try again shortly.",
                contact_values=contact_values,
            ), 503

        return render_template("contact.html", contact_status="sent", contact_values={})

    product = request.args.get("product", "").strip()
    if product:
        contact_values["message"] = f"I am interested in ordering: {product}."

    return render_template("contact.html", contact_values=contact_values)


if __name__ == "__main__":
    app.run(
        debug=True,
        port=5050
    )
