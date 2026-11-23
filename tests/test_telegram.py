from datetime import date, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from routers import telegram
from models import User
from schemas import AppointmentCreate, UserResponse


@pytest.fixture(autouse=True)
def clear_telegram_state():
    telegram.active_chats.clear()
    telegram.active_chat_dates.clear()
    telegram.processed_update_ids.clear()
    telegram.processed_update_order.clear()
    telegram.admin_message_clients.clear()
    telegram.admin_message_order.clear()
    yield


def test_resolve_upcoming_date_uses_today_or_next_year():
    assert telegram.resolve_upcoming_date(
        11, 23, today=date(2026, 11, 23)
    ) == date(2026, 11, 23)
    assert telegram.resolve_upcoming_date(
        1, 2, today=date(2026, 11, 23)
    ) == date(2027, 1, 2)


def test_resolve_upcoming_date_handles_leap_day():
    assert telegram.resolve_upcoming_date(
        2, 29, today=date(2025, 3, 1)
    ) == date(2028, 2, 29)


def test_resolve_start_time_replaces_past_year_and_uses_rome_timezone():
    resolved = telegram.resolve_start_time(
        "2024-12-10T10:30:00",
        now=datetime(2026, 11, 23, 12, 0, tzinfo=telegram.ROME),
    )

    assert resolved == datetime(2026, 12, 10, 10, 30, tzinfo=telegram.ROME)


def _function_call(name, args):
    return SimpleNamespace(name=name, args=args)


@pytest.mark.asyncio
async def test_function_call_flow_checks_availability_before_booking(monkeypatch):
    events = []
    slot_args = {
        "service_id": 4,
        "start_time_str": "2026-12-01T10:00:00+01:00",
    }

    async def check_availability(**kwargs):
        events.append("availability")
        return {
            "available": True,
            "service_id": kwargs["service_id"],
            "start_time": kwargs["start_time_str"],
        }

    async def book_appointment(**kwargs):
        events.append("booking")
        return "Success! Appointment successfully booked."

    monkeypatch.setattr(
        telegram, "check_nail_appointment_availability", check_availability
    )
    monkeypatch.setattr(telegram, "book_nail_appointment", book_appointment)

    class ChatSession:
        def __init__(self, responses):
            self.responses = responses
            self.sent_messages = []

        async def send_message(self, message):
            self.sent_messages.append(message)
            return self.responses.pop(0)

    # A booking call without a preceding availability result is rejected.
    chat_session = ChatSession(
        [SimpleNamespace(function_calls=[], text="Your appointment is booked.")]
    )
    initial_response = SimpleNamespace(
        function_calls=[
            _function_call(
                telegram.BOOK_APPOINTMENT_TOOL,
                {
                    **slot_args,
                    "client_name": "Elena Rossi",
                    "client_phone": "+393123456789",
                },
            )
        ],
        text=None,
    )
    final_response, booking_result, booking_succeeded = (
        await telegram._handle_function_calls(
            response=initial_response,
            chat_session=chat_session,
            db=object(),
        )
    )
    assert events == []
    assert booking_result is None
    assert booking_succeeded is False
    assert len(chat_session.sent_messages) == 1
    assert final_response.text == "Your appointment is booked."
    assert "can't confirm a booking" in telegram._final_reply_text(
        final_response,
        booking_result,
        booking_succeeded,
    )

    # The model must receive the availability result before it can book.
    events.clear()
    chat_session = ChatSession(
        [
            SimpleNamespace(
                function_calls=[
                    _function_call(
                        telegram.BOOK_APPOINTMENT_TOOL,
                        {
                            **slot_args,
                            "client_name": "Elena Rossi",
                            "client_phone": "+393123456789",
                        },
                    )
                ],
                text=None,
            ),
            SimpleNamespace(
                function_calls=[], text="Your appointment is booked."
            ),
        ]
    )
    initial_response = SimpleNamespace(
        function_calls=[
            _function_call(telegram.CHECK_AVAILABILITY_TOOL, slot_args)
        ],
        text=None,
    )
    final_response, booking_result, booking_succeeded = (
        await telegram._handle_function_calls(
            response=initial_response,
            chat_session=chat_session,
            db=object(),
        )
    )
    assert events == ["availability", "booking"]
    assert booking_result.startswith("Success!")
    assert booking_succeeded is True
    assert len(chat_session.sent_messages) == 2
    assert final_response.text == "Your appointment is booked."
    assert telegram._final_reply_text(
        final_response,
        booking_result,
        booking_succeeded,
    ) == final_response.text


@pytest.mark.asyncio
async def test_booking_rechecks_database_conflict(monkeypatch):
    async def get_service_by_id(**kwargs):
        return SimpleNamespace(duration=60)

    async def check_appointment_conflict(**kwargs):
        return True

    async def unexpected_create(**kwargs):
        pytest.fail("A conflicting appointment must not be created")

    monkeypatch.setattr(telegram.crud, "get_service_by_id", get_service_by_id)
    monkeypatch.setattr(
        telegram.crud, "check_appointment_conflict", check_appointment_conflict
    )
    monkeypatch.setattr(telegram.crud, "create_appointment", unexpected_create)

    result = await telegram.book_nail_appointment(
        service_id=4,
        start_time_str="2026-12-01T10:00:00+01:00",
        client_name="Elena Rossi",
        client_phone="+393123456789",
        db=object(),
    )

    assert "already booked" in result


@pytest.mark.asyncio
async def test_booking_creates_guest_without_email(monkeypatch):
    observed = {}

    async def get_service_by_id(**kwargs):
        return SimpleNamespace(duration=60)

    async def check_appointment_conflict(**kwargs):
        return False

    async def get_or_create_guest_user(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(id=12)

    async def create_appointment(**kwargs):
        observed["appointment"] = kwargs["appointment"]
        return SimpleNamespace(start_time=kwargs["appointment"].start_time)

    monkeypatch.setattr(telegram.crud, "get_service_by_id", get_service_by_id)
    monkeypatch.setattr(
        telegram.crud, "check_appointment_conflict", check_appointment_conflict
    )
    monkeypatch.setattr(
        telegram.crud, "get_or_create_guest_user", get_or_create_guest_user
    )
    monkeypatch.setattr(telegram.crud, "create_appointment", create_appointment)

    result = await telegram.book_nail_appointment(
        service_id=4,
        start_time_str="2026-12-01T10:00:00+01:00",
        client_name="Elena Rossi",
        client_phone="+393123456789",
        db=object(),
    )

    assert result.startswith("Success!")
    assert observed["client_email"] is None
    assert observed["appointment"].client_email is None


@pytest.mark.asyncio
async def test_booking_confirmation_uses_persisted_appointment_time(monkeypatch):
    async def get_service_by_id(**kwargs):
        return SimpleNamespace(duration=60)

    async def check_appointment_conflict(**kwargs):
        return False

    async def get_or_create_guest_user(**kwargs):
        return SimpleNamespace(id=12)

    async def create_appointment(**kwargs):
        return SimpleNamespace(
            start_time=datetime.fromisoformat("2026-12-10T09:00:00+00:00")
        )

    monkeypatch.setattr(telegram.crud, "get_service_by_id", get_service_by_id)
    monkeypatch.setattr(
        telegram.crud, "check_appointment_conflict", check_appointment_conflict
    )
    monkeypatch.setattr(
        telegram.crud, "get_or_create_guest_user", get_or_create_guest_user
    )
    monkeypatch.setattr(telegram.crud, "create_appointment", create_appointment)

    result = await telegram.book_nail_appointment(
        service_id=4,
        start_time_str="2026-12-10T10:00:00+01:00",
        client_name="Elena Rossi",
        client_phone="+393123456789",
        db=object(),
    )

    assert "2026-12-10 10:00 (Europe/Rome)" in result


@pytest.mark.asyncio
async def test_availability_check_uses_service_duration_and_database_conflicts(
    monkeypatch,
):
    observed = {}

    async def get_service_by_id(**kwargs):
        return SimpleNamespace(duration=45)

    async def check_appointment_conflict(**kwargs):
        observed.update(kwargs)
        return False

    monkeypatch.setattr(telegram.crud, "get_service_by_id", get_service_by_id)
    monkeypatch.setattr(
        telegram.crud, "check_appointment_conflict", check_appointment_conflict
    )
    start_time = datetime.fromisoformat("2026-12-01T10:00:00+01:00")

    result = await telegram.check_nail_appointment_availability(
        service_id=4,
        start_time_str=start_time.isoformat(),
        db=object(),
    )

    assert result["available"] is True
    assert observed["start_time"] == start_time
    assert (observed["end_time"] - observed["start_time"]).total_seconds() == 2700


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_appointment", [None, object()])
async def test_crud_conflict_check_handles_zero_or_multiple_results(
    existing_appointment,
):
    class FakeDatabase:
        async def execute(self, statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    first=lambda: existing_appointment
                )
            )

    has_conflict = await telegram.crud.check_appointment_conflict(
        db=FakeDatabase(),
        start_time=datetime.fromisoformat("2026-12-01T10:00:00+01:00"),
        end_time=datetime.fromisoformat("2026-12-01T11:00:00+01:00"),
    )

    assert has_conflict is (existing_appointment is not None)


def test_appointment_create_allows_omitted_client_email():
    appointment = AppointmentCreate(
        service_id=4,
        start_time=datetime.fromisoformat("2026-12-01T10:00:00+01:00"),
        client_name="Elena Rossi",
        client_phone="+393123456789",
    )

    assert appointment.client_email is None
    assert User.__table__.c.email.nullable is True
    assert UserResponse(
        id=1,
        username="Elena Rossi",
        email=None,
        phone="+393123456789",
    ).email is None


class _FakeTelegramResponse:
    def __init__(self, message_id):
        self.message_id = message_id

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "result": {"message_id": self.message_id}}


class _FakeTelegramClient:
    instances = []

    def __init__(self):
        self.requests = []
        self.next_message_id = 100
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json):
        self.requests.append((url, json))
        response = _FakeTelegramResponse(self.next_message_id)
        self.next_message_id += 1
        return response


class _FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_language"),
    [
        ("Qual è il vostro indirizzo?", "it"),
        ("Где вы находитесь? Нужен адрес", "ru"),
        ("What is your address?", "en"),
    ],
)
async def test_location_request_sends_localized_address_and_pin(
    monkeypatch, text, expected_language
):
    _FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeTelegramClient)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", SecretStr("test-token"))
    monkeypatch.setattr(telegram.settings, "salon_name", "Lena Nail Studio")
    monkeypatch.setattr(telegram.settings, "salon_address", "Via Roma 12, Rome")
    monkeypatch.setattr(telegram.settings, "salon_latitude", 41.9)
    monkeypatch.setattr(telegram.settings, "salon_longitude", 12.5)
    monkeypatch.setattr(telegram.settings, "google_maps_url", None)

    await telegram.telegram_webhook(
        _FakeRequest(
            {
                "update_id": 88,
                "message": {"chat": {"id": 123}, "text": text},
            }
        ),
        db=object(),
    )

    requests = _FakeTelegramClient.instances[0].requests
    assert len(requests) == 2
    message_url, message_payload = requests[0]
    location_url, location_payload = requests[1]
    assert message_url.endswith("/sendMessage")
    assert "Lena Nail Studio" in message_payload["text"]
    assert "Via Roma 12, Rome" in message_payload["text"]
    assert "https://www.google.com/maps/search/?api=1&query=" in message_payload["text"]
    assert location_url.endswith("/sendLocation")
    assert location_payload == {
        "chat_id": 123,
        "latitude": 41.9,
        "longitude": 12.5,
    }
    if expected_language == "it":
        assert "si trova" in message_payload["text"]
    elif expected_language == "ru":
        assert "находится" in message_payload["text"]
    else:
        assert "is located" in message_payload["text"]


@pytest.mark.asyncio
async def test_location_uses_explicit_maps_url(monkeypatch):
    monkeypatch.setattr(telegram.settings, "salon_name", "Lena Nail Studio")
    monkeypatch.setattr(telegram.settings, "salon_address", "Via Roma 12, Rome")
    monkeypatch.setattr(telegram.settings, "google_maps_url", "https://maps.example/salon")

    message = telegram._localized_location_message("en")

    assert "https://maps.example/salon" in message
    assert "google.com/maps/search" not in message


@pytest.mark.asyncio
async def test_duplicate_update_id_is_ignored(monkeypatch):
    _FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeTelegramClient)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", SecretStr("test-token"))
    monkeypatch.setattr(telegram.settings, "salon_latitude", None)
    monkeypatch.setattr(telegram.settings, "salon_longitude", None)
    request = _FakeRequest(
        {
            "update_id": 99,
            "message": {"chat": {"id": 123}, "text": "Where are you?"},
        }
    )

    await telegram.telegram_webhook(request, db=object())
    duplicate_result = await telegram.telegram_webhook(request, db=object())

    assert duplicate_result == {"ok": True, "duplicate": True}
    assert len(_FakeTelegramClient.instances[0].requests) == 1


@pytest.mark.asyncio
async def test_gemini_503_is_retried_with_backoff(monkeypatch):
    from google.genai.errors import ServerError

    class FlakyChat:
        def __init__(self):
            self.calls = 0

        async def send_message(self, _message):
            self.calls += 1
            if self.calls < 3:
                raise ServerError(
                    503,
                    {"error": {"status": "UNAVAILABLE", "message": "overloaded"}},
                )
            return "ok"

    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)
    chat = FlakyChat()

    result = await telegram._send_gemini_message(chat, "hello")

    assert result == "ok"
    assert chat.calls == 3
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_exhausted_gemini_503_sends_localized_fallback(monkeypatch):
    from google.genai.errors import ServerError

    _FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeTelegramClient)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", SecretStr("test-token"))

    class UnavailableChat:
        async def send_message(self, _message):
            raise ServerError(
                503,
                {"error": {"status": "UNAVAILABLE", "message": "raw overload details"}},
            )

    async def no_wait(_delay):
        return None

    monkeypatch.setattr(telegram.asyncio, "sleep", no_wait)
    telegram.active_chats[124] = UnavailableChat()
    telegram.active_chat_dates[124] = datetime.now(telegram.ROME).date()

    await telegram.telegram_webhook(
        _FakeRequest(
            {
                "update_id": 104,
                "message": {
                    "chat": {"id": 124},
                    "text": "Привет, хочу записаться",
                },
            }
        ),
        db=object(),
    )

    sent_text = _FakeTelegramClient.instances[0].requests[0][1]["text"]
    assert sent_text == "Произошла ошибка. Пожалуйста, попробуйте немного позже."
    assert "raw overload details" not in sent_text


@pytest.mark.asyncio
async def test_database_failure_sends_localized_fallback_without_details(monkeypatch):
    _FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeTelegramClient)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", SecretStr("test-token"))

    async def fail_get_services(**_kwargs):
        raise RuntimeError("SECRET SQL traceback details")

    monkeypatch.setattr(telegram.crud, "get_services", fail_get_services)

    await telegram.telegram_webhook(
        _FakeRequest(
            {
                "update_id": 103,
                "message": {
                    "chat": {"id": 123},
                    "text": "Vorrei prenotare un appuntamento",
                },
            }
        ),
        db=object(),
    )

    sent_text = _FakeTelegramClient.instances[0].requests[0][1]["text"]
    assert sent_text == "Si è verificato un problema. Riprova tra poco."
    assert "SECRET SQL" not in sent_text


@pytest.mark.asyncio
async def test_client_message_forwarding_stores_admin_to_client_mapping(monkeypatch):
    _FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeTelegramClient)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", SecretStr("test-token"))
    monkeypatch.setattr(telegram.settings, "admin_telegram_chat_id", 9001)
    monkeypatch.setattr(telegram.settings, "forward_client_messages_to_admin", True)

    class Chat:
        async def send_message(self, _text):
            return SimpleNamespace(function_calls=[], text="Hello from the bot.")

    client_chat_id = 456
    telegram.active_chats[client_chat_id] = Chat()
    telegram.active_chat_dates[client_chat_id] = datetime.now(telegram.ROME).date()

    await telegram.telegram_webhook(
        _FakeRequest(
            {
                "update_id": 120,
                "message": {
                    "message_id": 32,
                    "chat": {"id": client_chat_id},
                    "from": {
                        "id": 789,
                        "first_name": "Elena",
                        "last_name": "Rossi",
                        "username": "elena",
                    },
                    "text": "Can someone help me?",
                },
            }
        ),
        db=object(),
    )

    client_requests = [
        request
        for fake_client in _FakeTelegramClient.instances
        for request in fake_client.requests
    ]
    assert len(client_requests) == 2
    forwarded_url, forwarded_payload = client_requests[0]
    assert forwarded_url.endswith("/sendMessage")
    assert forwarded_payload["chat_id"] == 9001
    assert "Elena Rossi" in forwarded_payload["text"]
    assert "@elena" in forwarded_payload["text"]
    assert "Client chat_id: 456" in forwarded_payload["text"]
    assert "Can someone help me?" in forwarded_payload["text"]
    assert telegram.admin_message_clients[100] == client_chat_id


@pytest.mark.asyncio
async def test_admin_reply_is_delivered_to_mapped_client(monkeypatch):
    _FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeTelegramClient)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", SecretStr("test-token"))
    monkeypatch.setattr(telegram.settings, "admin_telegram_chat_id", 9001)
    telegram._store_admin_message_mapping(321, 456)

    await telegram.telegram_webhook(
        _FakeRequest(
            {
                "update_id": 121,
                "message": {
                    "message_id": 654,
                    "chat": {"id": 9001, "type": "private"},
                    "from": {"id": 9001, "first_name": "Admin"},
                    "text": "I can help you with that.",
                    "reply_to_message": {"message_id": 321},
                },
            }
        ),
        db=object(),
    )

    requests = _FakeTelegramClient.instances[0].requests
    assert requests[0] == (
        "https://api.telegram.org/bottest-token/sendMessage",
        {"chat_id": 456, "text": "I can help you with that."},
    )
    assert requests[1][1] == {
        "chat_id": 9001,
        "text": "Your reply was delivered to the client.",
    }


@pytest.mark.asyncio
async def test_sender_other_than_admin_cannot_route_admin_reply(monkeypatch):
    _FakeTelegramClient.instances.clear()
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _FakeTelegramClient)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", SecretStr("test-token"))
    monkeypatch.setattr(telegram.settings, "admin_telegram_chat_id", 9001)
    telegram._store_admin_message_mapping(321, 456)

    result = await telegram.telegram_webhook(
        _FakeRequest(
            {
                "update_id": 122,
                "message": {
                    "message_id": 655,
                    "chat": {"id": 9001, "type": "private"},
                    "from": {"id": 777, "first_name": "Imposter"},
                    "text": "Unauthorized message",
                    "reply_to_message": {"message_id": 321},
                },
            }
        ),
        db=object(),
    )

    assert result == {"ok": True}
    assert _FakeTelegramClient.instances == []


def test_database_engine_has_connection_health_pool_settings():
    from database import engine

    assert isinstance(engine, AsyncEngine)
    assert engine.pool._pre_ping is True
    assert engine.pool._recycle > 0
