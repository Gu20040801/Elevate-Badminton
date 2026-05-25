# Elevate Badminton Academy

Flask website for Elevate Badminton Academy.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 app.py
```

The local server runs at `http://127.0.0.1:5050`.

## Environment Variables

Set these in `.env` locally and in your hosting provider's environment settings:

```env
GMAIL_USER=elevatebadminton99@gmail.com
GMAIL_APP_PASSWORD=your-gmail-app-password
BOOKING_RECORDS_PATH=instance/bookings.csv
```

`GMAIL_APP_PASSWORD` must be a Gmail app password, not the normal Gmail login password.

## Production

Start command:

```bash
gunicorn app:app
```

Booking submissions are saved to `BOOKING_RECORDS_PATH` and notification emails are sent through Gmail SMTP.
