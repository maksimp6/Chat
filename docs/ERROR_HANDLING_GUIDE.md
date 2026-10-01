# Error Handling & Credential Sanitization Guide

## Overview

Alice Pro implements centralized error handling with automatic credential sanitization to prevent accidental exposure of API keys, tokens, and other sensitive data in logs, traces, and error responses.

## Core Components

### 1. SecureErrorHandler

Provides unified error handling with automatic credential masking in tracebacks.

**Location:** `error_handler.py`

#### Basic Usage

```python
from error_handler import SecureErrorHandler

@SecureErrorHandler.wrap_function(
    context="file_upload",
    user_message="Upload failed"
)
def process_file(file_path: str):
    # Your code here
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
```

#### Manual Error Handling

```python
try:
    result = risky_operation()
except Exception as e:
    error_response = SecureErrorHandler.handle_error(
        error=e,
        context="risky_operation",  # Identifies operation in logs
        log_level="error",
        return_code=500,
        user_message="Operation failed"  # Shown to user
    )
    return error_response, error_response["status_code"]
```

#### Features

- **Automatic Credential Masking:** Removes API keys, tokens, and passwords from traceback
- **Full Debug Logging:** Complete traceback with `exc_info=True` for debugging
- **Safe User Messages:** Returns generic messages to clients
- **Context Tracking:** Identifies which operation failed
- **HTTP Status Codes:** Support for 4xx and 5xx responses

### 2. CredentialSanitizer

Advanced sanitization that detects secrets by both key names and value patterns.

**Location:** `credential_sanitizer.py`

#### Detection Modes

**By Key Name:**
```python
from credential_sanitizer import CredentialSanitizer

# Detects these as secret keys
keys = ["api_key", "password", "access_token", "x-api-key"]
for key in keys:
    print(CredentialSanitizer.is_secret_key(key))  # True
```

**By Value Pattern:**
```python
# Detects high-entropy strings matching secret formats
values = [
    "sk-1234567890abcdef",  # OpenAI key format
    "AKIA1234567890123456",  # AWS key format
    "eyJhbGciOiJIUzI1NiI...",  # JWT token
]

for val in values:
    print(CredentialSanitizer.is_secret_value(val))  # True
```

#### Usage Examples

**Sanitizing Dictionary Data:**
```python
from credential_sanitizer import sanitize_for_logging

data = {
    "user": "john",
    "api_key": "sk-secret123",
    "nested": {
        "password": "mypassword",
        "email": "john@example.com"
    }
}

safe_data = sanitize_for_logging(data)
# Results in:
# {
#     "user": "john",
#     "api_key": "<CREDENTIALS MASKED>",
#     "nested": {
#         "password": "<CREDENTIALS MASKED>",
#         "email": "john@example.com"
#     }
# }
```

**Sanitizing Log Strings:**
```python
log_text = "API Key: sk-1234567890abcdefghij Password: secret123"
safe_log = CredentialSanitizer.sanitize_string(log_text)
# Results in: "API Key: <MASKED> Password: <MASKED>"
```

**Sanitizing Exceptions:**
```python
try:
    api_call()
except Exception as e:
    traceback_str = traceback.format_exc()
    safe_traceback = CredentialSanitizer.get_sanitized_traceback(traceback_str)
    logger.error(f"Operation failed:\n{safe_traceback}")
```

## Detected Secret Patterns

### Key Names (Case-Insensitive)
- `api_key`, `apikey`, `api-key`
- `access_token`, `refresh_token`, `auth_token`
- `authorization`, `bearer`
- `password`, `passwd`
- `secret`, `client_secret`
- `private_key`, `private-key`
- `credentials`, `credential`
- `x-api-key`, `x-auth-token`, `x-token`
- `jwt`

### Value Patterns
- **API Keys:** `sk-*`, `pk-*`, `api_*` followed by 20+ chars
- **Bearer Tokens:** `Bearer` followed by 20+ chars
- **JWT Tokens:** `eyJ*.eyJ*.` format
- **AWS Keys:** `AKIA` + 16 alphanumeric chars
- **Yandex Keys:** `YANDEX_API_KEY` or similar + 30+ chars
- **Base64/Hex:** 40+ character alphanumeric sequences (when not URLs or numbers)

## Best Practices

### 1. Use Decorators for Simple Functions
```python
@SecureErrorHandler.wrap_function(
    context="database_query",
    user_message="Database query failed"
)
def get_user_by_id(user_id: int):
    return db.query(f"SELECT * FROM users WHERE id = {user_id}")
```

### 2. Manual Handling for Complex Flows
```python
def complex_operation():
    try:
        step1_result = perform_step_1()
        step2_result = perform_step_2(step1_result)
        return step2_result
    except Step1Error as e:
        return SecureErrorHandler.handle_error(
            e, context="step_1", return_code=400,
            user_message="First step failed"
        )
    except Step2Error as e:
        return SecureErrorHandler.handle_error(
            e, context="step_2", return_code=500,
            user_message="Second step failed"
        )
```

### 3. Sanitize Before Logging
```python
# NEVER log raw request data
# BAD:
logger.info(f"Request: {request_data}")  # May contain secrets

# GOOD:
safe_data = sanitize_for_logging(request_data)
logger.info(f"Request: {safe_data}")  # Secrets masked
```

### 4. Use Context Parameter
```python
# Include operation context for debugging
SecureErrorHandler.handle_error(
    error=e,
    context="yandex_api.send_request",  # Hierarchical context
    user_message="API request failed"
)
```

## Testing Error Handling

### Test Setup
```python
from error_handler import SecureErrorHandler
from credential_sanitizer import CredentialSanitizer

def test_error_with_secret_in_message():
    """Ensure secrets are masked even in error messages"""
    try:
        api_key = "sk-test-secret-key-12345"
        raise ValueError(f"Invalid API key: {api_key}")
    except Exception as e:
        result = SecureErrorHandler.handle_error(
            error=e,
            context="test",
            user_message="Invalid credentials"
        )
        # Logs should have masked version
        assert result["success"] is False
```

### Test Patterns
```python
def test_credential_detection():
    """Test that various credential formats are detected"""
    secrets = [
        "sk-123456789",
        "AKIA1234567890123456",
        "eyJhbGciOiJIUzI1NiJ..."
    ]
    
    for secret in secrets:
        assert CredentialSanitizer.is_secret_value(secret)

def test_safe_values_not_masked():
    """Ensure legitimate data isn't masked"""
    safe_values = [
        "https://example.com",
        "user@example.com",
        "2024-01-15",
    ]
    
    for value in safe_values:
        assert not CredentialSanitizer.is_secret_value(value)
```

## Integration Points

### Flask/Web Routes
```python
from flask import Flask, jsonify
from error_handler import get_safe_error_response

@app.route("/api/data", methods=["POST"])
def create_data():
    try:
        # Process request
        pass
    except ValidationError as e:
        response, status = get_safe_error_response(400, "Invalid input")
        return jsonify(response), status
    except Exception as e:
        result = SecureErrorHandler.handle_error(
            e, context="create_data"
        )
        return jsonify(result), result["status_code"]
```

### Yandex Client
```python
from error_handler import SecureErrorHandler

class YandexClient:
    @SecureErrorHandler.wrap_function(
        context="yandex_api",
        user_message="API request failed"
    )
    def send_request(self, method, url, **kwargs):
        return requests.request(method, url, **kwargs)
```

### File Operations
```python
@SecureErrorHandler.wrap_function(
    context="file_upload",
    user_message="Upload failed",
    return_on_error={"success": False, "file_id": None}
)
def upload_file(file_content, filename):
    # Process file
    pass
```

## Monitoring & Debugging

### Log Levels
- `DEBUG`: Detailed operation flow
- `INFO`: Operation completion
- `WARNING`: Recoverable errors
- `ERROR`: Failed operations
- `CRITICAL`: System failures

### Traceback Analysis
Even with sanitization, full tracebacks are logged internally:
```
[system_operation] ValueError: Invalid parameter
Traceback (most recent call last):
  File "app.py", line 45, in process
    result = service.validate(data)  # Context preserved
  File "service.py", line 123, in validate
    if api_key == "<CREDENTIALS MASKED>":  # Secrets masked
      ...
```

## Common Issues & Solutions

### Issue: Secret Still Visible
**Solution:** Ensure sanitization is applied before logging
```python
# Wrong:
logger.error(f"Error: {str(exception)}")  # May contain secrets

# Right:
from credential_sanitizer import CredentialSanitizer
safe_error = CredentialSanitizer.sanitize_string(str(exception))
logger.error(f"Error: {safe_error}")
```

### Issue: User Gets Technical Error Message
**Solution:** Always provide generic user_message
```python
# Wrong:
return {"error": str(exception)}  # User sees internal details

# Right:
result = SecureErrorHandler.handle_error(
    exception,
    user_message="Operation failed"  # Generic message
)
```

### Issue: Can't Debug Production Error
**Solution:** Logs contain full traceback with exc_info=True
```python
# Full traceback is in logs, but sanitized for security
# Check logs with: grep "context_name" logs/alice_pro.log
```

## Compliance

This system helps meet security requirements for:
- **OWASP:** A01:2021 Broken Access Control (prevents credential exposure)
- **CWE:** CWE-532 Insertion of Sensitive Information into Log File
- **Data Protection:** GDPR/CCPA compliance in logs

## References

- [Error Handling Tests](../tests/test_error_handling_comprehensive.py)
- [Credential Sanitization Tests](../tests/test_credential_sanitization.py)
- [Source: error_handler.py](../error_handler.py)
- [Source: credential_sanitizer.py](../credential_sanitizer.py)
