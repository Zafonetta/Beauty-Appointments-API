🚀 Beauty Appointments REST API
A high-performance, asynchronous REST API for a beauty salon platform built with FastAPI, PostgreSQL, and SQLAlchemy 2.0.

✨ Key Features
JWT Authentication: Secure login, registration, role-based access (User/Admin), and session management.

Password Reset Flow: Token generation and automated transactional email dispatch.

Appointment & Service Management: Full CRUD capabilities for booking slots and services, optimized with eager loading to prevent N+1 query performance issues.

Database Migrations: Managed via Alembic.

Interactive Documentation: Integrated Swagger UI (/docs) and ReDoc (/redoc).

🛠️ Tech Stack
Framework: FastAPI

Database: PostgreSQL

ORM: SQLAlchemy 2.0 (Async)

Migrations: Alembic

Package Manager: uv / pip
