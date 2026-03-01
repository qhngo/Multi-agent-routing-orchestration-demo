from __future__ import annotations

import logging
from enum import Enum
import secrets

from src.app.repositories.interfaces import UserRepository, WebSessionRepository
from src.app.security.interfaces import PasswordHasherInterface


class RegisterStatus(str, Enum):
    SUCCESS = "success"
    USERNAME_TOO_SHORT = "username_too_short"
    PASSWORD_TOO_SHORT = "password_too_short"
    USER_EXISTS = "user_exists"


class LoginStatus(str, Enum):
    SUCCESS = "success"
    INVALID_CREDENTIALS = "invalid_credentials"


class AuthService:
    def __init__(
        self,
        user_repo: UserRepository,
        web_session_repo: WebSessionRepository,
        password_hasher: PasswordHasherInterface,
        logger: logging.Logger,
    ) -> None:
        self._user_repo = user_repo
        self._web_session_repo = web_session_repo
        self._password_hasher = password_hasher
        self._logger = logger

    def register_user(self, username: str, password: str) -> RegisterStatus:
        cleaned = username.strip()
        self._logger.debug("Registration attempt for username='%s'.", cleaned)
        if len(cleaned) < 3:
            self._logger.warning("Registration failed: username too short.")
            return RegisterStatus.USERNAME_TOO_SHORT
        if len(password) < 6:
            self._logger.warning("Registration failed for username='%s': password too short.", cleaned)
            return RegisterStatus.PASSWORD_TOO_SHORT
        password_hash = self._password_hasher.hash_password(password)
        created = self._user_repo.create_user(cleaned, password_hash)
        if not created:
            self._logger.warning("Registration failed for username='%s': already exists.", cleaned)
            return RegisterStatus.USER_EXISTS
        self._logger.info("Registration successful for username='%s'.", cleaned)
        return RegisterStatus.SUCCESS

    def login_user(self, username: str, password: str) -> tuple[LoginStatus, str | None]:
        self._logger.debug("Login attempt for username='%s'.", username)
        cleaned = username.strip()
        stored_hash = self._user_repo.get_password_hash(cleaned)
        if not stored_hash or not self._password_hasher.verify_password(password, stored_hash):
            self._logger.warning("Login rejected for username='%s'.", username)
            return LoginStatus.INVALID_CREDENTIALS, None
        token = secrets.token_urlsafe(24)
        self._web_session_repo.create_session(cleaned, token)
        self._logger.info(
            "Login successful for username='%s'. web_session_token='%s'",
            cleaned,
            token,
        )
        return LoginStatus.SUCCESS, token

    def logout_user(self, token: str | None) -> str | None:
        username = self._web_session_repo.delete_session(token)
        if username:
            self._logger.info("Logout for username='%s'.", username)
        else:
            self._logger.debug("Logout called without a valid session token.")
        return username

    def get_current_user(self, token: str | None) -> str | None:
        return self._web_session_repo.get_username(token)
