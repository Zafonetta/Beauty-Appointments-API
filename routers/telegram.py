import asyncio
from collections import deque
from datetime import date, datetime, time, timedelta
import logging
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import Request, APIRouter
import crud
from config import settings
from google import genai
from google.genai.errors import APIError
from google.genai import types
import models
from schemas import AppointmentCreate
from database import AsyncSessionLocal, DbSession
import schemas


router = APIRouter()

logger = logging.getLogger(__name__) # Module-scoped logger to trace events/errors by file in Cloud Run logs
ROME = ZoneInfo("Europe/Rome") # Rome timezone (automatically adjusts for summer/winter daylight saving)
GEMINI_MAX_ATTEMPTS = 3 # Max total tries when calling Gemini before giving up on transient errors (e.g. 503)
GEMINI_RETRY_BASE_SECONDS = 0.5 # Base wait (0.5s) between retry attempts to ease load on Gemini
UPDATE_ID_CACHE_SIZE = 10_000 # Max recent Telegram update_ids kept in memory to skip duplicate webhooks
ADMIN_FORWARD_CACHE_SIZE = 10_000 # Max recent admin-forwarded message mappings kept in memory for replies
MESSAGE_DEBOUNCE_SECONDS = 3.5
CHAT_HISTORY_LIMIT = 12
CHAT_SESSION_GAP = timedelta(hours=12)

# Initialize Gemini client
gemini_client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

# Build the Telegram API base URL using the SecretStr value
TELEGRAM_TOKEN = settings.telegram_bot_token.get_secret_value()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


active_chats = {} # Maps Telegram chat_id -> active chat session in RAM (resets on restart)
active_chat_dates: dict[int, date] = {} # Maps Telegram chat_id -> last Rome calendar date tracked for that chat
active_chat_last_activity: dict[int, datetime] = {}
processed_update_ids: set[int] = set() # Fast O(1) lookup set of recent Telegram webhook update_ids to skip retries
processed_update_order: deque[int] = deque() # FIFO double-ended queue to evict oldest update_ids past cache limit
admin_message_clients: dict[int, int] = {} # Maps admin chat message_id -> client chat_id so admin replies route back
admin_message_order: deque[int] = deque() # FIFO double-ended queue to evict oldest admin reply mappings past limit
pending_messages: dict[int, list[str]] = {}
debounce_tasks: dict[int, asyncio.Task] = {}
debounce_locks: dict[int, asyncio.Lock] = {}
turn_locks: dict[int, asyncio.Lock] = {}

# global constants (fixed configuration)
CHECK_AVAILABILITY_TOOL = "check_nail_appointment_availability"
BOOK_APPOINTMENT_TOOL = "book_nail_appointment"
FIND_UPCOMING_APPOINTMENTS_TOOL = "find_upcoming_appointments"
RESCHEDULE_BOOKING_TOOL = "reschedule_booking"
MAX_FUNCTION_CALL_ROUNDS = 5


def clean_telegram_text(text: str) -> str:
  # Remove Markdown bold markers (**) that show as literal asterisks in plain text[cite: 5, 7]
  cleaned = text.replace("**", "")
  # Optional: remove standalone single '*' used for italics
  cleaned = re.sub(r"(?<!\*)\*(?!\*)", "", cleaned)
  return cleaned.strip()


# Prevents picking a stale/wrong year (like 2024 instead of 2026)
def resolve_upcoming_date(
    month: int,
    day: int,
    today: date | None = None,
) -> date:
    """Resolve a month/day to its next occurrence, using the Rome calendar."""
    local_today = today or datetime.now(ROME).date()
    for year in range(local_today.year, local_today.year + 9):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= local_today:
            return candidate
    raise ValueError(f"No valid upcoming date for month={month}, day={day}")


def resolve_start_time(start_time_str: str, now: datetime | None = None) -> datetime:
    """Normalize a tool-requested timestamp to Europe/Rome and repair past years."""
    # Use passed `now` (e.g., in tests) or get the current time in Rome.
    current_time = now or datetime.now(ROME)

    # If `current_time` has no timezone (naive), attach ROME without changing clock numbers;
    # if it already has a timezone, convert it to ROME time.
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=ROME)
    else:
        current_time = current_time.astimezone(ROME)

    # Parse the ISO timestamp string sent by Gemini into a Python datetime object.
    requested = datetime.fromisoformat(start_time_str)
    # Normalize `requested` to ROME time (attach ROME if naive, or convert if another tz).
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=ROME)
    else:
        requested = requested.astimezone(ROME)

    # If the requested calendar date is in the past (e.g., Gemini used a stale year),
    # find the next valid upcoming date and keep the requested time of day.
    if requested.date() < current_time.date():
        upcoming_day = resolve_upcoming_date(
            requested.month,
            requested.day,
            today=current_time.date(),
        )
        requested = datetime.combine(
            upcoming_day,
            time(requested.hour, requested.minute, requested.second),
            tzinfo=ROME,
        )
    # Return the Clean datetime
    return requested


def _google_maps_url() -> str:
    # Return a custom/preconfigured Google Maps URL right away if one is set and non-empty.
    if settings.google_maps_url and settings.google_maps_url.strip():
        return settings.google_maps_url.strip()
    # Otherwise build a clean search query from the service name and address.
    query = f"{settings.salon_name} {settings.salon_address}".strip()
    # URL-encode query parameters so spaces and special characters form a valid Maps link.
    return f"https://www.google.com/maps/search/?{urlencode({'api': '1', 'query': query})}"


def _message_language(text: str) -> str:
    if re.search(r"[\u0400-\u04ff]", text):
        return "ru"
    if re.search(
        r"\b(indirizzo|dove|siete|posizione|indicazioni|arrivare|"
        r"vorrei|prenotare|appuntamento)\b",
        text,
        re.IGNORECASE,
    ):
        return "it"
    return "en"


def _is_location_request(text: str) -> bool:
    # lowercase normalization (normalizing accented/special letters)
    normalized = text.casefold()
    location_phrases = (
        "address",
        "location",
        "directions",
        "where are you",
        "where are you located",
        "where is the salon",
        "how do i get",
        "how to get there",
        "how can i get there",
        "directions to",
        "indirizzo",
        "dove siete",
        "dove si trova",
        "in che zona",
        "posizione",
        "indicazioni",
        "come arrivare",
        "адрес",
        "где вы",
        "где находится",
        "местоположение",
        "как добраться",
        "как вас найти",
    )
    # Checks if any string in location_phrases appears as a substring inside normalized
    return any(phrase in normalized for phrase in location_phrases)


def _localized_location_message(language: str) -> str:
    maps_url = _google_maps_url()
    if language == "it":
        return f"{settings.salon_name} si trova in {settings.salon_address}.\nMappa: {maps_url}"
    if language == "ru":
        return f"{settings.salon_name} находится по адресу: {settings.salon_address}.\nКарта: {maps_url}"
    return f"{settings.salon_name} is located at {settings.salon_address}.\nMap: {maps_url}"


# Returns a polite, user-facing fallback message in the user's language when something fails behind the scenes
def _localized_error_message(language: str) -> str:
    if language == "it":
        return "Si è verificato un problema. Riprova tra poco."
    if language == "ru":
        return "Произошла ошибка. Пожалуйста, попробуйте немного позже."
    return "Something went wrong. Please try again shortly."


def _is_transient_gemini_error(error: APIError) -> bool:
    # 503 = temporarily unavailable; 429 = rate/burst/capacity limit (often transient).
    if error.code == (429, 503):
        return True
    # Normalize status and message safely to avoid NoneType errors and case mismatches.
    status_text = str(error.status or "").casefold()
    message_text = str(error.message or "").casefold()

    # Treat overload/unavailable signals as transient rather than permanent client/auth bugs.
    transient_markers = (
        "overload",
        "unavailable",
        "resource_exhausted",
        "rate limit",
        "try again",
    )
    return any(
        marker in status_text or marker in message_text
        for marker in transient_markers
    )


async def _send_gemini_message(chat_session, message):
    for attempt in range(GEMINI_MAX_ATTEMPTS):
        try:
            return await chat_session.send_message(message)
        except APIError as exc:
            if (
                attempt + 1 == GEMINI_MAX_ATTEMPTS
                or not _is_transient_gemini_error(exc)
            ):
                raise
            await asyncio.sleep(GEMINI_RETRY_BASE_SECONDS * (2**attempt))
    raise RuntimeError("Gemini retry loop ended unexpectedly.")


def _mark_update_processed(update_id: int) -> bool:
    # Skip duplicate Telegram updates that are already in the recent cache.
    if update_id in processed_update_ids:
        return False
    # Record the new update ID for O(1) lookup and keep arrival order in a deque.
    processed_update_ids.add(update_id)
    processed_update_order.append(update_id)
    # Evict the oldest update ID (FIFO) once the cache exceeds its max size.
    if len(processed_update_order) > UPDATE_ID_CACHE_SIZE:
        expired_update_id = processed_update_order.popleft()
        processed_update_ids.discard(expired_update_id)
    # True means this update is new and should be processed.
    return True


# Helper that sanitizes outgoing text and sends it to the Telegram Bot
async def _send_telegram_message(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: int,
    text: str,
) -> dict:
    # Clean/format the outgoing text before sending it to Telegram.
    cleaned_text = clean_telegram_text(text)
    # POST the message payload asynchronously via the shared HTTP client.
    response = await client.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": cleaned_text},
    )
    # Raise an HTTP error if Telegram returned a 4xx/5xx status code.
    response.raise_for_status()
    # Parse JSON and verify Telegram explicitly accepted the message (`ok` not False).
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is False:
        raise RuntimeError("Telegram sendMessage request was not accepted.")
    return result


# Checks whether an incoming Telegram chat_id matches configured admin chat ID
def _is_admin_chat(chat_id: int) -> bool:
    return (
        settings.admin_telegram_chat_id is not None
        and chat_id == settings.admin_telegram_chat_id
    )


def _is_authorized_admin_message(message: dict) -> bool:
    # Fail closed if no admin ID is configured.
    admin_id = settings.admin_telegram_chat_id
    if admin_id is None:
        return False
    # Safely read both the conversation ID and the actual sender's user ID.
    chat_id = message.get("chat", {}).get("id")
    sender_id = message.get("from", {}).get("id")
    # Require both chat and sender to match so another user cannot spoof admin context.
    return chat_id == admin_id and sender_id == admin_id


# bounded FIFO lookup mapping from an admin_message_id to the client_chat_id it belongs to.
def _store_admin_message_mapping(admin_message_id: int, client_chat_id: int) -> None:
    # Keep the first recorded mapping if this admin message ID is already stored.
    if admin_message_id in admin_message_clients:
        return
    # Map the admin chat message ID to the originating client's chat ID in O(1).
    admin_message_clients[admin_message_id] = client_chat_id
    admin_message_order.append(admin_message_id)
    # Evict the oldest entry (FIFO) to cap memory at ADMIN_FORWARD_CACHE_SIZE.
    if len(admin_message_order) > ADMIN_FORWARD_CACHE_SIZE:
        expired_message_id = admin_message_order.popleft()
        admin_message_clients.pop(expired_message_id, None)


# formats an incoming client message and sends a summary to your admin Telegram chat
async def _forward_client_message(
    client: httpx.AsyncClient,
    bot_token: str,
    message: dict,
) -> None:
    # Exit early if forwarding is disabled or no admin chat is configured.
    admin_chat_id = settings.admin_telegram_chat_id
    if (
        not settings.forward_client_messages_to_admin
        or admin_chat_id is None
    ):
        return

    # Build a readable sender label and optional @username line.
    sender = message.get("from", {})
    first_name = sender.get("first_name", "")
    last_name = sender.get("last_name", "")
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    username = sender.get("username")
    sender_name = full_name or (f"@{username}" if username else "Unknown")
    username_line = f"\nUsername: @{username}" if username else ""

    # Extract text or caption, with a fallback label for uncaptioned media.
    message_text = message.get("text") or message.get("caption") or "[non-text message]"
    summary = (
        "Forwarded client message\n"
        f"Name: {sender_name}{username_line}\n"
        f"Client chat_id: {message['chat']['id']}\n\n"
        f"{message_text}"
    )

    # Send summary to the admin chat and map the returned message ID to the client.
    result = await _send_telegram_message(
        client,
        bot_token,
        admin_chat_id,
        summary,
    )
    admin_message_id = result.get("result", {}).get("message_id")
    if isinstance(admin_message_id, int):
        _store_admin_message_mapping(admin_message_id, message["chat"]["id"])
    else:
        logger.warning("Telegram forward response had no admin message_id.")


# delivers reply text to that client, and sends a confirmation back to your admin chat.
async def _handle_admin_message(
    client: httpx.AsyncClient,
    bot_token: str,
    message: dict,
) -> None:
    # Find the message ID the admin replied to and look up the originating client.
    reply_to_message = message.get("reply_to_message") or {}
    forwarded_message_id = reply_to_message.get("message_id")
    client_chat_id = (
        admin_message_clients.get(forwarded_message_id)
        if isinstance(forwarded_message_id, int)
        else None
    )
    if client_chat_id is None:
        # Either the admin didn't reply to a forwarded message or the cache entry expired.
        confirmation = (
            "I couldn't find the client for that forwarded message. "
            "Please reply directly to a recent forwarded client message."
        )
    else:
        reply_text = message.get("text") or message.get("caption")
        if not reply_text:
            confirmation = "Please reply with text so I can deliver it to the client."
        else:
            # Deliver the admin's reply to the client chat and confirm back to admin.
            await _send_telegram_message(
                client,
                bot_token,
                client_chat_id,
                reply_text,
            )
            confirmation = "Your reply was delivered to the client."

    await _send_telegram_message(
        client,
        bot_token,
        settings.admin_telegram_chat_id,
        confirmation,
    )


# optional geo-pin sender
async def _send_location_if_configured(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: int,
) -> None:
    # Skip sending a map pin if either coordinate is missing (check `is None` so 0.0 stays valid).
    if settings.salon_latitude is None or settings.salon_longitude is None:
        logger.warning("Salon coordinates are not configured; sending map link only.")
        return

    # POST latitude and longitude to Telegram's sendLocation endpoint.
    response = await client.post(
        f"https://api.telegram.org/bot{bot_token}/sendLocation",
        json={
            "chat_id": chat_id,
            "latitude": settings.salon_latitude,
            "longitude": settings.salon_longitude,
        },
    )
    # Raise an HTTP error if Telegram rejects the request.
    response.raise_for_status()

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
                            description="ID of the requested service.",
                        ),
                        "start_time_str": types.Schema(
                            type=types.Type.STRING,
                            description="Requested start time in ISO 8601 format.",
                        ),
                        "appointment_id": types.Schema(
                            type=types.Type.INTEGER,
                            description=(
                                "Existing appointment ID to exclude when checking "
                                "a reschedule; omit for a new booking."
                            ),
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
                            description="Client's full name if not already on file.",
                        ),
                        "client_phone": types.Schema(
                            type=types.Type.STRING,
                            description="Client's phone if not already on file.",
                        ),
                    },
                    required=["service_id", "start_time_str"],
                ),
            ),
            types.FunctionDeclaration(
                name=FIND_UPCOMING_APPOINTMENTS_TOOL,
                description=(
                    "Find the current Telegram user's upcoming appointments. "
                    "Call this before any reschedule and ask for clarification if "
                    "more than one appointment is returned."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={},
                ),
            ),
            types.FunctionDeclaration(
                name=RESCHEDULE_BOOKING_TOOL,
                description=(
                    "Reschedule one of the Telegram user's upcoming appointments. "
                    "Only call after finding the appointment and checking the new "
                    "time for that appointment ID."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "appointment_id": types.Schema(
                            type=types.Type.INTEGER,
                            description="ID of the appointment to reschedule.",
                        ),
                        "start_time_str": types.Schema(
                            type=types.Type.STRING,
                            description="New requested start time in ISO 8601 format.",
                        ),
                    },
                    required=["appointment_id", "start_time_str"],
                ),
            ),
        ]
    )
]

# a private, internal helper function, can't be imported or called from other files
def _slot_key(
    service_id: int,
    start_time_str: str,
    appointment_id: int | None = None,
) -> tuple[int, str, int | None]:
    return (
        service_id,
        resolve_start_time(start_time_str).isoformat(),
        appointment_id,
    )


# Function calling - Telegram bot calls before allowing Gemini to book an appointment
async def check_nail_appointment_availability(
    service_id: int,
    start_time_str: str,
    db: DbSession,
    telegram_user_id: str | None = None,
    appointment_id: int | None = None,
) -> dict[str, bool | int | str]:
    # Parses the requested start time and checks if required service exists
    start_time = resolve_start_time(start_time_str)
    service = await crud.get_service_by_id(db=db, service_id=service_id)
    if not service:
        return {
            "available": False,
            "service_id": service_id,
            "message": "The requested service does not exist.",
        }

    if appointment_id is not None:
        if telegram_user_id is None:
            return {
                "available": False,
                "service_id": service_id,
                "message": "Could not verify the appointment owner.",
            }
        _, upcoming_appointments = (
            await crud.get_upcoming_appointments_for_telegram_user(
                db=db,
                telegram_user_id=telegram_user_id,
                now=datetime.now(ROME),
            )
        )
        appointment = next(
            (
                item
                for item in upcoming_appointments
                if item.id == appointment_id
            ),
            None,
        )
        if appointment is None or appointment.service_id != service_id:
            return {
                "available": False,
                "service_id": service_id,
                "message": "The appointment could not be verified.",
            }

    # Calculate end_time and checks for conflict
    end_time = start_time + timedelta(minutes=service.duration)
    has_conflict = await crud.check_appointment_conflict(
        db=db,
        start_time=start_time,
        end_time=end_time,
        exclude_appointment_id=appointment_id,
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


async def find_upcoming_appointments(
    db: DbSession,
    telegram_user_id: str | None,
) -> dict:
    if telegram_user_id is None:
        return {"found": False, "appointments": []}
    user, appointments = await crud.get_upcoming_appointments_for_telegram_user(
        db=db,
        telegram_user_id=telegram_user_id,
        now=datetime.now(ROME),
    )
    if user is None:
        return {"found": False, "appointments": []}
    return {
        "found": bool(appointments),
        "appointments": [
            {
                "appointment_id": appointment.id,
                "service_id": appointment.service_id,
                "service": appointment.service.name,
                "start_time": appointment.start_time.isoformat(),
                "end_time": appointment.end_time.isoformat(),
            }
            for appointment in appointments
        ],
    }


async def reschedule_booking(
    db: DbSession,
    telegram_user_id: str | None,
    appointment_id: int,
    start_time_str: str,
    available_slots: set[tuple[int, str, int | None]],
) -> str:
    if telegram_user_id is None:
        return "I couldn't verify your profile. Please contact the salon to reschedule."
    user, appointments = await crud.get_upcoming_appointments_for_telegram_user(
        db=db,
        telegram_user_id=telegram_user_id,
        now=datetime.now(ROME),
    )
    if user is None:
        return "I couldn't find a profile linked to this Telegram account."
    appointment = next(
        (item for item in appointments if item.id == appointment_id),
        None,
    )
    if appointment is None:
        return "I couldn't find that upcoming appointment on your profile."

    new_start_time = resolve_start_time(start_time_str)
    slot_key = _slot_key(
        appointment.service_id,
        new_start_time.isoformat(),
        appointment_id,
    )
    if slot_key not in available_slots:
        return (
            "I haven't checked that new time for this appointment yet. "
            "Check availability before rescheduling."
        )

    old_start_time = appointment.start_time
    try:
        updated = await crud.update_appointment(
            db=db,
            appointment=appointment,
            appointment_update=schemas.AppointmentUpdate(
                start_time=new_start_time
            ),
        )
    except crud.AppointmentConflictError:
        return "That time is no longer available. Please choose another time."
    if updated is None:
        return "I couldn't update that appointment. Please contact the salon."
    available_slots.remove(slot_key)

    old_local = old_start_time.astimezone(ROME) if old_start_time.tzinfo else old_start_time.replace(tzinfo=ROME)
    new_local = updated.start_time.astimezone(ROME) if updated.start_time.tzinfo else updated.start_time.replace(tzinfo=ROME)
    return (
        f"Success! Appointment #{appointment_id} was rescheduled from "
        f"{old_local.strftime('%Y-%m-%d %H:%M')} to "
        f"{new_local.strftime('%Y-%m-%d %H:%M')} (Europe/Rome)."
    )


# The Security Guard & Loop Manager: stopping Gemini from trying to fake a booking or skipping steps
async def _handle_function_calls(
    response: types.GenerateContentResponse,
    chat_session,
    db: DbSession,
    telegram_user_id: str | None = None,
    profile=None,
) -> tuple[types.GenerateContentResponse, str | None, bool]:
    available_slots: set[tuple[int, str, int | None]] = set()
    booking_result: str | None = None
    booking_succeeded = False

    # Implements a circuit breaker to prevent infinite loops (e.g., repeatedly calling availability or failing to parse arguments correctly)
    for _ in range(MAX_FUNCTION_CALL_ROUNDS):
        if not response.function_calls:
            break

        function_responses = []
        # storing verified slots as unique tuples (service_id, normalized_time)
        newly_available_slots: set[tuple[int, str, int | None]] = set()

        # If Gemini passed arguments, use them; otherwise, default to an empty dictionary {} to keep the code safe
        for call in response.function_calls:
            args = call.args or {}
            # Handle Availability Checks
            if call.name == CHECK_AVAILABILITY_TOOL:
                service_id = int(args["service_id"])
                start_time = resolve_start_time(args["start_time_str"])
                start_time_str = start_time.isoformat()
                appointment_id = (
                    int(args["appointment_id"])
                    if args.get("appointment_id") is not None
                    else None
                )
                result = await check_nail_appointment_availability(
                    service_id=service_id,
                    start_time_str=start_time_str,
                    db=db,
                    telegram_user_id=telegram_user_id,
                    appointment_id=appointment_id,
                )
                if result["available"]:
                    newly_available_slots.add(
                        _slot_key(service_id, start_time_str, appointment_id)
                    )
            # Handle Booking Execution (Secured by Slot-Lock Check)
            elif call.name == BOOK_APPOINTMENT_TOOL:
                service_id = int(args["service_id"])
                start_time = resolve_start_time(args["start_time_str"])
                start_time_str = start_time.isoformat()
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
                        client_name=(
                            args.get("client_name")
                            or (profile.username if profile else "")
                        ),
                        client_phone=(
                            args.get("client_phone")
                            or (profile.phone if profile else "")
                        ),
                        db=db,
                        telegram_user_id=telegram_user_id,
                    )
                    booking_succeeded = result.startswith("Success!")
                    booking_result = result
            elif call.name == FIND_UPCOMING_APPOINTMENTS_TOOL:
                result = await find_upcoming_appointments(
                    db=db,
                    telegram_user_id=telegram_user_id,
                )
            elif call.name == RESCHEDULE_BOOKING_TOOL:
                result = await reschedule_booking(
                    db=db,
                    telegram_user_id=telegram_user_id,
                    appointment_id=int(args["appointment_id"]),
                    start_time_str=args["start_time_str"],
                    available_slots=available_slots,
                )
                booking_result = result
                booking_succeeded = result.startswith("Success!")
            else:
                raise ValueError(f"Unsupported Gemini function: {call.name}")

            function_responses.append(
                types.Part.from_function_response(
                    name=call.name,
                    response={"result": result},
                )
            )

        # Send tool outputs back to Gemini for the next conversational turn
        response = await _send_gemini_message(chat_session, function_responses)
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
        db: DbSession,
        telegram_user_id: str | None = None,
) -> str:
    """
    Creates a new nail appointment in the database after verifying availability.

    Args:
        service_id: The ID of the nail service being booked.
        start_time_str: The requested start time in ISO format (e.g., '2026-06-10T10:00:00').
        client_name: Full name of the client.
        client_phone: Phone number of the client.
    """
    start_time = resolve_start_time(start_time_str)

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
    if telegram_user_id is not None:
        await crud.link_telegram_user_to_client(
            db=db,
            user_id=user.id,
            telegram_user_id=telegram_user_id,
        )

    appointment = await crud.create_appointment(
        db=db,
        appointment=appointment_data,
        user_id=user.id,
        end_time=end_time,
    )

    persisted_start_time = appointment.start_time
    if persisted_start_time.tzinfo is None:
        persisted_start_time = persisted_start_time.replace(tzinfo=ROME)
    else:
        persisted_start_time = persisted_start_time.astimezone(ROME)
    return (
        f"Success! Appointment successfully booked for {client_name} at "
        f"{persisted_start_time.strftime('%Y-%m-%d %H:%M')} (Europe/Rome)."
    )


def _chat_history_contents(messages: list[models.ChatMessage]) -> list[types.Content]:
    return [
        types.Content(
            role=message.role,
            parts=[types.Part.from_text(text=message.content)],
        )
        for message in messages
        if message.role in {"user", "model"}
    ]


async def _process_client_turn(
    chat_id: int,
    telegram_user_id: str,
    user_text: str,
) -> None:
    language = _message_language(user_text)
    bot_token = settings.telegram_bot_token.get_secret_value()
    reply_text: str

    try:
        async with AsyncSessionLocal() as db:
            current_local_time = datetime.now(ROME)
            current_local_date = current_local_time.date()
            chat_session = active_chats.get(chat_id)
            profile = await crud.get_user_by_telegram_id(
                db=db,
                telegram_user_id=telegram_user_id,
            )
            if (
                chat_session is None
                or active_chat_dates.get(chat_id) != current_local_date
                or (
                    active_chat_last_activity.get(chat_id) is not None
                    and current_local_time - active_chat_last_activity[chat_id]
                    >= CHAT_SESSION_GAP
                )
            ):
                history = await crud.get_recent_chat_messages(
                    db=db,
                    telegram_user_id=telegram_user_id,
                    now=current_local_time,
                    limit=CHAT_HISTORY_LIMIT,
                    session_gap=CHAT_SESSION_GAP,
                )
                services_list, _ = await crud.get_services(db=db)
                services_text = "\n".join(
                    f"- {service.name}: service_id = {service.id} "
                    f"({service.duration} min, €{service.price})"
                    for service in services_list
                )
                maps_url = _google_maps_url()
                profile_context = (
                    f"\nKnown client name: {profile.username}."
                    " Use the stored contact profile when booking; do not ask "
                    "for details already on file."
                    if profile
                    else ""
                )
                system_instruction = (
                    "You are the virtual receptionist for "
                    f"{settings.salon_name} in Rome.\n"
                    f"Current local date and time: "
                    f"{current_local_time.isoformat(timespec='minutes')} "
                    "(Europe/Rome). Resolve relative dates using this context; never "
                    "invent a past year. For a month/day without a year, use its next "
                    "occurrence on or after the current local date.\n"
                    f"Salon street address: {settings.salon_address}\n"
                    f"Google Maps: {maps_url}\n"
                    f"Available salon services from our database:\n{services_text}\n"
                    f"{profile_context}\n\n"
                    "For every new booking, collect the service, date, time, name, "
                    "and phone number unless already available in the verified client "
                    "profile. Check availability before booking. Never confirm a "
                    "booking unless its tool reports success.\n"
                    "For rescheduling, first call "
                    f"`{FIND_UPCOMING_APPOINTMENTS_TOOL}`. If there are multiple "
                    "upcoming appointments, ask which one before continuing. For the "
                    "selected appointment, call "
                    f"`{CHECK_AVAILABILITY_TOOL}` with its appointment_id, service_id, "
                    "and requested new time. Only after the result confirms availability "
                    f"call `{RESCHEDULE_BOOKING_TOOL}` with that appointment ID and "
                    "time. Confirm both the previous and new times only after success.\n"
                    "Do not treat availability from earlier user turns as current. "
                    "Answer address questions using the configured salon address and map."
                )
                chat_session = gemini_client.aio.chats.create(
                    model="gemini-3.6-flash",
                    history=_chat_history_contents(history),
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=TELEGRAM_TOOLS,
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(
                                mode="AUTO"
                            )
                        ),
                    ),
                )
                active_chats[chat_id] = chat_session
                active_chat_dates[chat_id] = current_local_date

            await crud.save_chat_message(
                db=db,
                telegram_user_id=telegram_user_id,
                role="user",
                content=user_text,
            )
            response = await _send_gemini_message(chat_session, user_text)
            response, booking_result, booking_succeeded = await _handle_function_calls(
                response=response,
                chat_session=chat_session,
                db=db,
                telegram_user_id=telegram_user_id,
                profile=profile,
            )
            reply_text = _final_reply_text(
                response=response,
                booking_result=booking_result,
                booking_succeeded=booking_succeeded,
            )
            await crud.save_chat_message(
                db=db,
                telegram_user_id=telegram_user_id,
                role="model",
                content=reply_text,
            )
            active_chat_last_activity[chat_id] = datetime.now(ROME)
    except Exception:
        logger.exception("Telegram conversation turn failed")
        reply_text = _localized_error_message(language)

    try:
        async with httpx.AsyncClient() as client:
            await _send_telegram_message(client, bot_token, chat_id, reply_text)
    except httpx.HTTPError:
        logger.exception("Failed to deliver Telegram reply")


async def _flush_message_batch(chat_id: int, telegram_user_id: str) -> None:
    try:
        await asyncio.sleep(MESSAGE_DEBOUNCE_SECONDS)
        buffer_lock = debounce_locks[chat_id]
        async with buffer_lock:
            current_task = asyncio.current_task()
            if debounce_tasks.get(chat_id) is not current_task:
                return
            messages = pending_messages.pop(chat_id, [])
            debounce_tasks.pop(chat_id, None)
        if not messages:
            return

        # A distinct lock serializes turns for one Gemini chat while still
        # allowing incoming updates to buffer during an in-flight model call.
        turn_lock = turn_locks.setdefault(chat_id, asyncio.Lock())
        async with turn_lock:
            await _process_client_turn(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                user_text="\n".join(messages),
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to flush Telegram message batch")


async def _enqueue_message(chat_id: int, telegram_user_id: str, text: str) -> None:
    buffer_lock = debounce_locks.setdefault(chat_id, asyncio.Lock())
    async with buffer_lock:
        pending_messages.setdefault(chat_id, []).append(text)
        previous_task = debounce_tasks.get(chat_id)
        if previous_task is not None and not previous_task.done():
            previous_task.cancel()
        debounce_tasks[chat_id] = asyncio.create_task(
            _flush_message_batch(chat_id, telegram_user_id)
        )


@router.post("/webhook")
async def telegram_webhook(request: Request, db: DbSession):
    """
        Receives incoming updates from Telegram via webhook,
        processes the text with genai, and replies back asynchronously.
    """
    data = await request.json()
    update_id = data.get("update_id")
    if isinstance(update_id, int) and not _mark_update_processed(update_id):
        return {"ok": True, "duplicate": True}

    message = data.get("message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    sender_id = message.get("from", {}).get("id")

    if _is_admin_chat(chat_id):
        if not _is_authorized_admin_message(message):
            logger.warning("Ignoring Telegram message from unauthorized admin-chat sender.")
            return {"ok": True}
        bot_token = settings.telegram_bot_token.get_secret_value()
        try:
            async with httpx.AsyncClient() as client:
                await _handle_admin_message(client, bot_token, message)
        except Exception:
            logger.exception("Failed to process admin Telegram reply")
            try:
                async with httpx.AsyncClient() as client:
                    await _send_telegram_message(
                        client,
                        bot_token,
                        settings.admin_telegram_chat_id,
                        "I couldn't deliver that reply. Please try again.",
                    )
            except httpx.HTTPError:
                logger.exception("Failed to send admin delivery-error confirmation")
        return {"ok": True}

    if (
        settings.admin_telegram_chat_id is not None
        and sender_id == settings.admin_telegram_chat_id
    ):
        logger.warning("Ignoring admin sender message outside the configured admin chat.")
        return {"ok": True}

    user_text = message.get("text") or message.get("caption") or ""

    language = _message_language(user_text)
    bot_token = settings.telegram_bot_token.get_secret_value()
    if settings.forward_client_messages_to_admin and settings.admin_telegram_chat_id is not None:
        try:
            async with httpx.AsyncClient() as client:
                await _forward_client_message(client, bot_token, message)
        except Exception:
            logger.exception("Failed to forward client message to admin")

    if not user_text:
        return {"ok": True}

    if _is_location_request(user_text):
        try:
            async with httpx.AsyncClient() as client:
                await _send_telegram_message(
                    client,
                    bot_token,
                    chat_id,
                    _localized_location_message(language),
                )
                try:
                    await _send_location_if_configured(
                        client, bot_token, chat_id
                    )
                except httpx.HTTPError:
                    logger.exception("Failed to deliver Telegram map pin")
        except Exception:
            logger.exception("Failed to answer Telegram location request")
        return {"ok": True}

    # Background turns own their database session; never retain the request-
    # scoped FastAPI dependency after acknowledging the webhook.
    await _enqueue_message(
        chat_id=chat_id,
        telegram_user_id=str(sender_id or chat_id),
        text=user_text,
    )
    return {"ok": True, "queued": True}
