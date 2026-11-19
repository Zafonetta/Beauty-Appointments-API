from datetime import datetime
from types import SimpleNamespace
import pytest

from routers import telegram
from models import User
from schemas import AppointmentCreate, UserResponse


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
