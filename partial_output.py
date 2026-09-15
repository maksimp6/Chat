"""
Shared response content extraction for both normal and error handling.

This module provides a single source of truth for extracting text content
from Yandex Responses API response objects, used by both:
1. extract_reasoning_and_text() for normal processing
2. extract_last_response_text() for partial output recovery

This avoids parser duplication per ТЗ section 3.2.
"""
from typing import Tuple, Optional, Dict, Any


def _extract_text_from_output_items(output_items: list) -> str:
    """
    Extract text content from Responses API output items.
    
    Processes:
    - Reasoning text (kept internal, returned separately if needed)
    - Output text / plain text (user-facing output)
    - Function/tool calls (ignored as not text output)
    
    Args:
        output_items: List of output items from response.output
        
    Returns:
        Concatenated non-reasoning text content
    """
    if not isinstance(output_items, list):
        return ""
    
    text_parts = []
    
    for item in output_items:
        if not isinstance(item, dict):
            continue
        
        # Process message items with content
        if item.get("type") == "message":
            content_list = item.get("content", [])
            if isinstance(content_list, list):
                for part in content_list:
                    if isinstance(part, dict):
                        p_type = part.get("type")
                        p_text = part.get("text", "")
                        # Skip reasoning_text, only grab output_text or text
                        if p_type in ("output_text", "text") and p_text:
                            text_parts.append(p_text)
                    elif isinstance(part, str):
                        text_parts.append(part)
        
        # Process direct output_text at item level
        elif item.get("type") == "output_text":
            if item.get("text"):
                text_parts.append(str(item["text"]))
    
    return "".join(text_parts)


def _extract_reasoning_from_output_items(output_items: list) -> str:
    """
    Extract reasoning content from Responses API output items.
    Reasoning is internal and not shown to user in partial output.
    
    Args:
        output_items: List of output items from response.output
        
    Returns:
        Concatenated reasoning text
    """
    if not isinstance(output_items, list):
        return ""
    
    reasoning_parts = []
    
    for item in output_items:
        if not isinstance(item, dict):
            continue
        
        if item.get("type") == "message":
            content_list = item.get("content", [])
            if isinstance(content_list, list):
                for part in content_list:
                    if isinstance(part, dict):
                        if part.get("type") == "reasoning_text" and part.get("text"):
                            reasoning_parts.append(part["text"])
    
    return "\n\n".join(reasoning_parts)


def extract_reasoning_and_text(data: Dict[str, Any]) -> Tuple[str, str]:
    """
    Extract reasoning and text from Responses API response.
    
    This is the canonical implementation used by yandex_client.extract_reasoning_and_text().
    Kept compatible with existing code while supporting partial output extraction.
    
    Args:
        data: Response dict from Responses API
        
    Returns:
        Tuple of (reasoning_text, output_text)
    """
    if not isinstance(data, dict):
        return "", str(data or "")
    
    output_items = data.get("output", [])
    
    # Extract using shared logic
    text = _extract_text_from_output_items(output_items)
    reasoning = _extract_reasoning_from_output_items(output_items)
    
    # Fallback to top-level fields
    if not text:
        text = data.get("output_text") or data.get("text") or ""
    
    return reasoning, str(text)


def extract_last_response_text(responses: list) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Extract the last non-empty text from ExecutionTrace.responses[].
    
    Used for partial output recovery when /api/chat encounters an error.
    Iterates responses in reverse order to find the most recent non-empty output.
    
    Args:
        responses: List of response dicts from ExecutionTrace.responses[]
        
    Returns:
        Tuple of (text, response_dict):
        - text: The last non-empty text found, or empty string
        - response_dict: The response entry containing this text, or None
    """
    if not isinstance(responses, list):
        return "", None
    
    # Iterate in reverse to find the most recent response with text
    for response in reversed(responses):
        if not isinstance(response, dict):
            continue
        
        raw = response.get("raw")
        if not isinstance(raw, dict):
            continue
        
        # Use shared text extraction from output
        text = _extract_text_from_output_items(raw.get("output", []))
        
        # Fallback to top-level fields
        if not text:
            text = (raw.get("output_text") or raw.get("text") or "").strip()
        else:
            text = text.strip()
        
        if text:
            return text, response
    
    return "", None


def format_partial_output_message(partial_output: str, error: str) -> str:
    """
    Format a message combining partial output with error notice.
    
    Per spec (section 2.2):
    - If partial output exists: <output>\n\n⚠️ Ошибка: <error>
    - If no partial output: ⚠️ Ошибка: <error>
    
    Args:
        partial_output: The partial text generated by the model (may be empty)
        error: The error message
        
    Returns:
        Formatted message string
    """
    if not partial_output:
        return f"⚠️ Ошибка: {error}"
    
    return f"{partial_output}\n\n⚠️ Ошибка: {error}"
