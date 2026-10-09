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

* 📖 **Interactive Swagger UI Docs:** [https://beauty-api-67591101232.europe-west1.run.app/docs](https://beauty-api-67591101232.europe-west1.run.app/docs)

## 🛠️ Tech Stack & Infrastructure
* **Framework:** FastAPI (Python 3.13)
* **Database:** PostgreSQL (Neon) with Async SQLAlchemy & Alembic migrations
* **Containerization:** Docker
* **Cloud Hosting:** Google Cloud Run & Artifact Registry
* **Package Manager:** uv / pip
