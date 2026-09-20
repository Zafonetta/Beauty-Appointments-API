import sys
import asyncio
from database import AsyncSessionLocal
from models import Admin
from security import hash_password
from config import settings

async def create_admin():
    async with AsyncSessionLocal() as db:
        # Extract plain string from SecretStr using .get_secret_value()
        raw_password = settings.admin_password.get_secret_value()
        admin_email = settings.admin_email

        hashed_password = hash_password(raw_password)

        new_admin = Admin(email=admin_email, password_hash=hashed_password)

        db.add(new_admin)
        await db.commit()
        print("✅ Admin created from .env settings!")

if __name__ == "__main__":
    # Fix for psycopg async on Windows
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(create_admin())