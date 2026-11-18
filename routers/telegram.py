from datetime import datetime, timedelta
import re
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

# global constants (fixed configuration)
CHECK_AVAILABILITY_TOOL = "check_nail_appointment_availability"
BOOK_APPOINTMENT_TOOL = "book_nail_appointment"
MAX_FUNCTION_CALL_ROUNDS = 5

# collection of tools available to use in this chat session
TELEGRAM_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                # blueprint of the arguments Gemini is allowed to send back to backend
                name=CHECK_AVAILABILITY_TOOL,
                description=(
                    "Check the database for overlapping appointments for a service "
                    "at the requested start time. Always call this before booking."
                ),
                parameters=types.Schema(
                    # arguments must be formatted as a structured JSON object
                    type=types.Type.OBJECT,
                    # key/argument that Gemini needs to extract from the user's text
                    properties={
                        "service_id": types.Schema(
                            type=types.Type.INTEGER,
                            description="ID of the requested salon service.",
                        ),
                        "start_time_str": types.Schema(
                            type=types.Type.STRING,
                            description="Requested start time in ISO 8601 format.",
                        ),
                    },
                    # If arguments are missing, Gemini will ask the user for it instead of calling the tool prematurely
                    required=["service_id", "start_time_str"],
                ),
            ),
            types.FunctionDeclaration(
                name=BOOK_APPOINTMENT_TOOL,
                description=(
                    "Create an appointment after the availability tool has confirmed "
                    "the same service and start time are free."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "service_id": types.Schema(
                            type=types.Type.INTEGER,
                            description="ID of the requested salon service.",
                        ),
                        "start_time_str": types.Schema(
                            type=types.Type.STRING,
                            description="Requested start time in ISO 8601 format.",
                        ),
                        "client_name": types.Schema(
                            type=types.Type.STRING,
                            description="Client's full name.",
                        ),
                        "client_phone": types.Schema(
                            type=types.Type.STRING,
                            description="Client's phone number.",
                        ),
                    },
                    required=[
                        "service_id",
                        "start_time_str",
                        "client_name",
                        "client_phone",
                    ],
                ),
            ),
        ]
    )
]

# a private, internal helper function, can't be imported or called from other files
def _slot_key(service_id: int, start_time_str: str) -> tuple[int, str]:
    """
    Parses and normalizes the timestamp into a standard ISO format
    and returns a tuple suitable for use as a dictionary key in slot-tracking logic
    """
    return service_id, datetime.fromisoformat(start_time_str).isoformat()


# Function calling - Telegram bot calls before allowing Gemini to book an appointment
async def check_nail_appointment_availability(
    service_id: int,
    start_time_str: str,
    db: DbSession,
) -> dict[str, bool | int | str]:
    # Parses the requested start time and checks if required service exists
    start_time = datetime.fromisoformat(start_time_str)
    service = await crud.get_service_by_id(db=db, service_id=service_id)
    if not service:
        return {
            "available": False,
            "service_id": service_id,
            "message": "The requested service does not exist.",
        }

    # Calculate end_time and checks for conflict
    end_time = start_time + timedelta(minutes=service.duration)
    has_conflict = await crud.check_appointment_conflict(
        db=db,
        start_time=start_time,
        end_time=end_time,
    )
    return {
        "available": not has_conflict,
        "service_id": service_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "message": (
            "The requested time is available."
            if not has_conflict
            else "The requested time is already booked."
        ),
    }


# The Security Guard & Loop Manager: stopping Gemini from trying to fake a booking or skipping steps
async def _handle_function_calls(
    response: types.GenerateContentResponse,
    chat_session,
    db: DbSession,
) -> tuple[types.GenerateContentResponse, str | None, bool]:
    available_slots: set[tuple[int, str]] = set()
    booking_result: str | None = None
    booking_succeeded = False

    # Implements a circuit breaker to prevent infinite loops (e.g., repeatedly calling availability or failing to parse arguments correctly)
    for _ in range(MAX_FUNCTION_CALL_ROUNDS):
        if not response.function_calls:
            break

        function_responses = []
        # storing verified slots as unique tuples (service_id, normalized_time)
        newly_available_slots = set()

        # If Gemini passed arguments, use them; otherwise, default to an empty dictionary {} to keep the code safe
        for call in response.function_calls:
            args = call.args or {}
            # Handle Availability Checks
            if call.name == CHECK_AVAILABILITY_TOOL:
                service_id = int(args["service_id"])
                start_time_str = args["start_time_str"]
                result = await check_nail_appointment_availability(
                    service_id=service_id,
                    start_time_str=start_time_str,
                    db=db,
                )
                if result["available"]:
                    newly_available_slots.add(
                        _slot_key(service_id, start_time_str)
                    )
            # Handle Booking Execution (Secured by Slot-Lock Check)
            elif call.name == BOOK_APPOINTMENT_TOOL:
                service_id = int(args["service_id"])
                start_time_str = args["start_time_str"]
                slot_key = _slot_key(service_id, start_time_str)

                # SECURITY GUARD: Block bookings for unverified slots
                if slot_key not in available_slots:
                    result = (
                        "Booking was not performed. First check availability for "
                        "this exact service and time, then wait for the result."
                    )
                else:
                    # Remove slot from verified set to prevent double booking attempts
                    available_slots.remove(slot_key)
                    result = await book_nail_appointment(
                        service_id=service_id,
                        start_time_str=start_time_str,
                        client_name=args["client_name"],
                        client_phone=args["client_phone"],
                        db=db,
                    )
                    booking_succeeded = result.startswith("Success!")
                    booking_result = result
            else:
                raise ValueError(f"Unsupported Gemini function: {call.name}")

            function_responses.append(
                types.Part.from_function_response(
                    name=call.name,
                    response={"result": result},
                )
            )

        # Send tool outputs back to Gemini for the next conversational turn
        response = await chat_session.send_message(function_responses)
        # A booking call in the same model response as an availability call is
        # rejected above; only a subsequent response can use this confirmation.
        available_slots.update(newly_available_slots)
    else:
        if response.function_calls:
            raise RuntimeError("Gemini exceeded the maximum function-call rounds.")

    return response, booking_result, booking_succeeded


# Regular Expression (RegEx): scans text for words indicating a booking confirmation
UNVERIFIED_BOOKING_CONFIRMATION = re.compile(
    r"\b(?:booked|reserved|scheduled|confirmed|"
    r"prenotat[oaie]?|confermat[oaie]?|fissat[oaie]?)\b",
    re.IGNORECASE,
)


def _final_reply_text(
    response: types.GenerateContentResponse,
    booking_result: str | None,
    booking_succeeded: bool,
) -> str:
    reply_text = response.text or ""
    # An attempt to book was made, but it failed (e.g., the slot was already taken, or it hit a database error)
    if booking_result and not booking_succeeded:
        return booking_result
    # The booking succeeded (booking_succeeded = True), but for some reason,
    # Gemini didn't generate a text response, or its response is empty.
    if booking_succeeded and not reply_text:
        return booking_result or "Your appointment was booked successfully."
    # Anti-Fake Confirmation
    if (
        not booking_succeeded
        and UNVERIFIED_BOOKING_CONFIRMATION.search(reply_text)
    ):
        return (
            "I can't confirm a booking yet. Please provide the requested details "
            "so I can check availability and complete the booking."
        )
    return reply_text


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
    start_time = datetime.fromisoformat(start_time_str)

    service = await crud.get_service_by_id(db=db, service_id=service_id)
    if not service:
        return "Error: The requested service does not exist."

    end_time = start_time + timedelta(minutes=service.duration)

    # Recheck at write time so a slot taken after the availability check is rejected.
    has_conflict = await crud.check_appointment_conflict(
        db=db, start_time=start_time, end_time=end_time
    )
    if has_conflict:
        return "Sorry, that time slot is already booked. Please choose another time."

    if not client_phone or not client_phone.strip():
        return "A valid client phone number is required to book an appointment."

    appointment_data = AppointmentCreate(
        service_id=service_id,
        start_time=start_time,
        client_name=client_name,
        client_phone=client_phone,
    )

    user = await crud.get_or_create_guest_user(
        db=db,
        client_name=client_name.strip(),
        client_phone=client_phone.strip(),
        client_email=None,
    )

    await crud.create_appointment(
        db=db,
        appointment=appointment_data,
        user_id=user.id,
        end_time=end_time,
    )

    return (
        f"Success! Appointment successfully booked for {client_name} at "
        f"{start_time.strftime('%Y-%m-%d %H:%M')}."
    )


@router.post("/webhook")
async def telegram_webhook(request: Request, db: DbSession):
    """
        Receives incoming updates from Telegram via webhook,
        processes the text with genai, and replies back asynchronously.
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
                    "You are the virtual receptionist for Lena's nail salon in Rome.\n"
                    f"Available salon services from our database:\n{services_text}\n\n"
                    "For every booking request, collect the service, date, time, full name, "
                    "and phone number. If any are missing, ask for them and do not claim "
                    "a booking. Before booking, call "
                    f"`{CHECK_AVAILABILITY_TOOL}` for the exact requested service and time, "
                    "then wait for its database result. Call "
                    f"`{BOOK_APPOINTMENT_TOOL}` only if that result says the slot is "
                    "available and you have all client details. Never claim an appointment "
                    "is booked unless the booking tool returns a success result. If the "
                    "slot is unavailable, explain that and ask for another time. Do not "
                    "treat availability from earlier conversation turns as current."
                ),
                tools=TELEGRAM_TOOLS,
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="AUTO"
                    )
                )
            )
        )

    chat_session = active_chats[chat_id]

    try:
        # The callable database functions are manually dispatched below; only
        # declarations are passed to Gemini, so the SDK cannot execute a stub.
        response = await chat_session.send_message(user_text)
        response, booking_result, booking_succeeded = await _handle_function_calls(
            response=response,
            chat_session=chat_session,
            db=db,
        )
        reply_text = _final_reply_text(
            response=response,
            booking_result=booking_result,
            booking_succeeded=booking_succeeded,
        )

    except Exception as e:
        print("❌ WEBHOOK ERROR:", str(e))
        reply_text = f"Sorry, I encountered an error: {str(e)}"


    # Send the response back to Telegram
    bot_token = settings.telegram_bot_token.get_secret_value()
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": reply_text}
        )

    return {"ok": True}
