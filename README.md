# 🚀 Beauty Appointments REST API
A high-performance, asynchronous REST API for a beauty salon platform built with FastAPI, PostgreSQL, and SQLAlchemy 2.0.

## ✨ Key Features
* **JWT Authentication:** Secure login, registration, role-based access (User/Admin), and session management.
* **Password Reset Flow:** Token generation and automated transactional email dispatch.
* **Appointment & Service Management:** Full CRUD capabilities for booking slots and services, optimized with eager loading to prevent N+1 query performance issues.
* **Database Migrations:** Managed via Alembic.
* **Interactive Documentation:** Integrated Swagger UI (`/docs`) and ReDoc (`/redoc`).

## 🚀 Live Demo & API Documentation
The application is fully containerized and deployed on **Google Cloud Run** connected to a serverless **Neon PostgreSQL** database.

* 📖 **Interactive Swagger UI Docs:** [Open Swagger API Docs](https://beauty-api-675911101232.europe-west1.run.app)

## 🛠️ Tech Stack & Infrastructure
* **Framework:** FastAPI (Python 3.13)
* **Database:** PostgreSQL (Neon) with Async SQLAlchemy & Alembic migrations
* **Containerization:** Docker
* **Cloud Hosting:** Google Cloud Run & Artifact Registry
* **Package Manager:** uv / pip

## Telegram salon configuration

Set the following environment variables for local runs (for example, in a
`.env` file based on `.env.example`) and configure the same values as Cloud Run
service environment variables or Secret Manager references:

* `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` for bot messaging and AI responses.
* `SALON_NAME` and `SALON_ADDRESS` for accurate assistant answers. The address
  defaults to `Rome, Italy`; set it to the salon's full street address.
* `SALON_LATITUDE` and `SALON_LONGITUDE` to enable Telegram's location pin.
  Leave both unset to send the address and map link without a pin.
* `GOOGLE_MAPS_URL` is optional. When unset, the app URL-encodes the salon name
  and address into a Google Maps search link.
* `ADMIN_TELEGRAM_CHAT_ID` optionally enables the human-reply bridge. Set it
  to the administrator's private Telegram chat/user ID. Forwarding of client
  messages is enabled by default when this ID is configured; set
  `FORWARD_CLIENT_MESSAGES_TO_ADMIN=false` to disable forwarding while keeping
  admin reply handling enabled. The admin must reply directly to a forwarded
  message in that private chat.

The assistant resolves dates and times in the `Europe/Rome` timezone. Gemini
503/overload errors are retried a limited number of times, and duplicate
Telegram `update_id` values are ignored per running application instance.
Admin reply mappings are held in a bounded in-memory cache, so replies work
across requests handled by the same Cloud Run instance. A redeploy, instance
restart, or request routed to another instance clears or cannot see that map.
