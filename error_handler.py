"""Unified error handling with secure logging and traceback management"""

import logging
import traceback
import sys
from typing import Any, Dict, Optional, Tuple
from functools import wraps

logger = logging.getLogger("alice_pro.errors")


class SecureErrorHandler:
    """Centralized error handling with credential sanitization"""

    _SECRET_PATTERNS = {
        "api_key", "apikey", "api-key",
        "authorization", "bearer",
        "token", "access_token", "refresh_token",
        "password", "passwd",
        "secret", "client_secret",
        "private_key", "private-key",
        "credentials", "credential",
        "x-api-key", "x-auth-token",
    }

    @staticmethod
    def _mask_value(value: Any) -> str:
        """Convert value to string and mask if it looks like a secret"""
        if not isinstance(value, str):
            return str(value)

        # Mask long strings that look like secrets
        if len(value) > 20 and not any(c in value for c in [' ', '\n', '.']):
            return f"<MASKED:{len(value)} chars>"
        return value

    @staticmethod
    def _sanitize_traceback(tb_lines: list) -> list:
        """Remove sensitive values from traceback"""
        sanitized = []
        for line in tb_lines:
            # Mask common secret patterns
            if any(secret in line.lower() for secret in SecureErrorHandler._SECRET_PATTERNS):
                # Keep the line structure but mask values
                if '=' in line:
                    parts = line.split('=', 1)
                    sanitized.append(f"{parts[0]}=<MASKED>")
                else:
                    sanitized.append("<MASKED SENSITIVE DATA>")
            else:
                sanitized.append(line)
        return sanitized

    @classmethod
    def handle_error(
        cls,
        error: Exception,
        context: str = "",
        log_level: str = "error",
        return_code: int = 500,
        user_message: str = "An error occurred"
    ) -> Dict[str, Any]:
        """
        Handle error with secure logging and sanitization

        Args:
            error: Exception to handle
            context: Context description (e.g., "file_upload", "api_call")
            log_level: Logging level (error, warning, critical)
            return_code: HTTP status code to return
            user_message: Safe message for client

        Returns:
            Dict with error response and metadata
        """
        exc_type, exc_value, exc_tb = sys.exc_info()

        # Get traceback lines
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)

        # Sanitize traceback
        safe_tb_lines = cls._sanitize_traceback(tb_lines)

        # Log with full traceback
        log_func = getattr(logger, log_level)
        log_func(
            f"[{context}] {error.__class__.__name__}: {error}",
            extra={"traceback": "".join(safe_tb_lines)}
        )

        # Return safe error response
        return {
            "success": False,
            "error": user_message,
            "error_type": error.__class__.__name__,
            "status_code": return_code,
            "context": context
        }

    @classmethod
    def wrap_function(
        cls,
        context: str = "",
        user_message: str = "Request failed",
        return_on_error: Any = None
    ):
        """
        Decorator to wrap functions with error handling

        Usage:
            @SecureErrorHandler.wrap_function(
                context="file_upload",
                user_message="Upload failed"
            )
            def upload_file(path):
                ...
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_response = cls.handle_error(
                        error=e,
                        context=context or func.__name__,
                        user_message=user_message
                    )
                    return return_on_error if return_on_error is not None else error_response
            return wrapper
        return decorator


def get_safe_error_response(
    status_code: int = 500,
    message: str = "Internal server error"
) -> Tuple[Dict[str, str], int]:
    """Generate safe error response without exposing internals"""
    safe_messages = {
        400: "Invalid request",
        401: "Unauthorized",
        403: "Access denied",
        404: "Not found",
        408: "Request timeout",
        413: "Payload too large",
        429: "Too many requests",
        500: "Internal server error",
        503: "Service unavailable",
    }

    safe_message = safe_messages.get(status_code, message)
    return {"error": safe_message}, status_code
