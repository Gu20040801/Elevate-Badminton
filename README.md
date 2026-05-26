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
RESEND_API_KEY=your-resend-api-key
RESEND_FROM=Elevate Badminton <onboarding@resend.dev>
BOOKING_RECORDS_PATH=instance/bookings.csv
```

Production email uses Resend over HTTPS. `RESEND_FROM` can use `onboarding@resend.dev`
for testing. For production, verify `elevatebadminton.com` in Resend and update
`RESEND_FROM` to an address on that domain.

## Production

Start command:

```bash
gunicorn app:app
```

Booking submissions are saved to `BOOKING_RECORDS_PATH` and notification emails are sent through Resend.
