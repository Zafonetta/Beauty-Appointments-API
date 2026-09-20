from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#Configuration Logic
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  #  Safely ignores extra/unmatched env variables
        env_file_encoding="utf-8",#even if you have special characters in your keys or passwords, Python reads them correctly.
    )
    database_url: str
#Security Defaults
    secret_key: SecretStr #It tells Pydantic that this value is a secret
    algorithm: str = "HS256" #This is the encryption standard used for your JWTs, "HS256" is the standard, reliable algorithm for signing tokens.
    access_token_expire_minutes: int = 30
    reset_token_expire_minutes: int = 60

    # Admin Seed Credentials
    admin_email: str
    admin_password: SecretStr  # Using SecretStr prevents it from leaking in logs


    appointments_per_page: int = 10
    services_per_page: int = 10
    users_per_page: int = 10


mail_server: str = "localhost"  # To send an email, our Python backend must log into a mail server (mail provider)
mail_port: int = 587  # The secure connection port standard for modern, encrypted web mail
mail_username: str = ""  # The email address our app logs into to send messages.
mail_password: SecretStr = SecretStr("")
mail_from: str = "noreply@example.com"
mail_use_tls: bool = True  # Forces the mail data to be encrypted while moving across the web

frontend_url: str = "http://localhost:8000"  # our app will use this base URL to build a clean link inside the email

#The Global Instance
settings = Settings()