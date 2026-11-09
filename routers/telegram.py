from datetime import datetime, timedelta
import httpx
from fastapi import Request, APIRouter
import crud
from config import settings
from google import genai
from google.genai import types
from schemas import AppointmentCreate
from database import DbSession


router = APIRouter()

# Initialize Gemini client
gemini_client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

# Build the Telegram API base URL using the SecretStr value
TELEGRAM_TOKEN = settings.telegram_bot_token.get_secret_value()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


# Store active chat sessions in memory, keyed by Telegram chat_id
active_chats = {}

# Define tool function that talks to database
async def book_nail_appointment(
        service_id: int,
        start_time_str: str,
        client_name: str,
        client_phone: str,
        db: DbSession  # We pass db when WE call it, not when Gemini defines parameters
) -> str:
    """
    Creates a new nail appointment in the database after verifying availability.

    Args:
        service_id: The ID of the nail service being booked.
        start_time_str: The requested start time in ISO format (e.g., '2026-06-10T10:00:00').
        client_name: Full name of the client.
        client_phone: Phone number of the client.
    """
    # for debug:
    print("🔥 >>> PYTHON DATABASE TOOL WAS ACTUALLY TRIGGERED! <<< 🔥")

    try:
        start_time = datetime.fromisoformat(start_time_str)

        # 1. Fetch service duration (reusing your logic)
        service = await crud.get_service_by_id(db=db, service_id=service_id)
        if not service:
            return "Error: The requested service does not exist."

        end_time = start_time + timedelta(minutes=service.duration)

        # 2. Check for time slot overlap
        has_conflict = await crud.check_appointment_conflict(
            db=db, start_time=start_time, end_time=end_time
        )
        if has_conflict:
            return "Sorry, that time slot is already booked. Please choose another time."

        # 3. Get or create guest user
        user = await crud.get_or_create_guest_user(
            db=db,
            client_name=client_name,
            client_phone=client_phone,
            client_email=None,
        )

        # 4. Create the appointment object structure
        appointment_data = AppointmentCreate(
            service_id=service_id,
            start_time=start_time,
            client_name=client_name,
            client_phone=client_phone,
        )

        # 5. Save to a database
        new_appointment = await crud.create_appointment(
            db=db,
            appointment=appointment_data,
            user_id=user.id,
            end_time=end_time,
        )

        return f"Success! Appointment successfully booked for {client_name} at {start_time.strftime('%Y-%m-%d %H:%M')}."

    except Exception as e:
        return f"Failed to create appointment due to an error: {str(e)}"


# Tool declaration for Gemini schema (without db parameter)
def book_nail_appointment_tool(
    service_id: int,
    start_time_str: str,
    client_name: str,
    client_phone: str
) -> str:
    """
    Creates a new nail appointment in the database after verifying availability.

    Args:
        service_id: The ID of the nail service being booked.
        start_time_str: The requested start time in ISO format (e.g., '2026-11-10T10:00:00').
        client_name: Full name of the client.
        client_phone: Phone number of the client.
    """
    pass


@router.post("/webhook")
async def telegram_webhook(request: Request, db: DbSession):
    """
        Receives incoming updates from Telegram via webhook,
        processes the text with OpenAI, and replies back asynchronously.
    """
    data = await request.json()

    message = data.get("message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    user_text = message.get("text", "")

    if not user_text:
        return {"ok": True}

    # Get or create a persistent chat session for this specific Telegram user
    # Initialize chat session dynamically with live services from Neon
    if chat_id not in active_chats:
        # Unpack the (services, total) tuple returned by your CRUD function
        services_list, total = await crud.get_services(db=db)
        services_text = "\n".join(
            [f"- {s.name}: service_id = {s.id} ({s.duration} min, €{s.price})" for s in services_list]
        )

        active_chats[chat_id] = gemini_client.aio.chats.create(
            model="gemini-3.6-flash",

            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a friendly, professional virtual receptionist for Lena's nail in Rome. "
                    "Your job is to help clients inquire about manicure services, opening hours, and booking appointments. "
                     "When a client wants to book, collect their preferred service, date, time, name, and phone number."
                ),
                tools=[book_nail_appointment_tool]
            )
        )

    chat_session = active_chats[chat_id]

    try:
        # 2. Send a message to the ongoing chat session (retains full memory of past turns!)
        response = await chat_session.send_message(user_text)
        # 4. Intercept and execute tool calls if Gemini requests them
        if response.function_calls:
            for call in response.function_calls:
                if call.name == "book_nail_appointment_tool":
                    args = call.args
                    result_msg = await book_nail_appointment(
                        service_id=int(args["service_id"]),
                        start_time_str=args["start_time_str"],
                        client_name=args["client_name"],
                        client_phone=args["client_phone"],
                        db=db
                    )

                    final_response = await chat_session.send_message(
                        types.Part.from_function_response(
                            name=call.name,
                            response={"result": result_msg}
                        )
                    )
                    reply_text = final_response.text
        else:
            reply_text = response.text

    except Exception as e:
        reply_text = f"Sorry, I encountered an error: {str(e)}"


    # Send the response back to Telegram
    bot_token = settings.telegram_bot_token.get_secret_value()
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": reply_text}
        )

    return {"ok": True}
