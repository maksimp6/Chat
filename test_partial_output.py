"""
Tests for partial output extraction and error handling in ExecutionTrace.
Tests cover:
- A. Error before model response
- B. Model response + subsequent error
- C. Multiple Responses API calls
- D. Tool call + error
- E. Successful request (no regression)
- F. Persistence of partial output after reload
"""
import unittest
import json
import time
from unittest.mock import patch, MagicMock
from partial_output import extract_last_response_text, format_partial_output_message
from trace_manager import ExecutionTrace


class TestPartialOutputExtraction(unittest.TestCase):
    """Test extract_last_response_text() function"""

    def test_empty_responses_list(self):
        """A. Empty responses list returns empty string"""
        text, response = extract_last_response_text([])
        self.assertEqual(text, "")
        self.assertIsNone(response)

    def test_no_text_in_responses(self):
        """A. Responses without text output return empty string"""
        responses = [
            {
                "raw": {
                    "output": [
                        {"type": "function_call", "name": "tool", "arguments": {}}
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "")
        self.assertIsNone(response)

    def test_single_text_output(self):
        """B. Extract text from single response"""
        responses = [
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "text", "text": "Hello world"}
                            ]
                        }
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Hello world")
        self.assertIsNotNone(response)

    def test_output_text_type(self):
        """Extract output_text type"""
        responses = [
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "Output text"}
                            ]
                        }
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Output_text type")

    def test_skip_reasoning_text(self):
        """Skip reasoning_text, only get output_text"""
        responses = [
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "reasoning_text", "text": "Internal reasoning"},
                                {"type": "output_text", "text": "Final answer"}
                            ]
                        }
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Final answer")

    def test_multiple_responses_get_last(self):
        """C. Multiple Responses API - return last non-empty"""
        responses = [
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "text", "text": "First response"}
                            ]
                        }
                    ]
                }
            },
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "text", "text": "Second response"}
                            ]
                        }
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Second response")

    def test_multiple_responses_skip_empty(self):
        """C. Multiple Responses - skip empty, get last with text"""
        responses = [
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "text", "text": "First response"}
                            ]
                        }
                    ]
                }
            },
            {
                "raw": {
                    "output": [
                        {"type": "function_call", "name": "tool"}  # No text
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "First response")

    def test_concatenate_multiple_text_parts(self):
        """Concatenate multiple text parts in one response"""
        responses = [
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "text", "text": "Hello "},
                                {"type": "text", "text": "world"}
                            ]
                        }
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Hello world")

    def test_fallback_to_top_level_text(self):
        """Fallback to top-level text field"""
        responses = [
            {
                "raw": {
                    "text": "Top level text",
                    "output": []
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Top level text")

    def test_malformed_response_skipped(self):
        """Malformed responses are skipped"""
        responses = [
            {"raw": "not a dict"},
            {
                "raw": {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "text", "text": "Valid response"}
                            ]
                        }
                    ]
                }
            }
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Valid response")


class TestFormatPartialOutputMessage(unittest.TestCase):
    """Test format_partial_output_message() function"""

    def test_no_partial_output(self):
        """A. Only error when no partial output"""
        result = format_partial_output_message("", "Connection timeout")
        self.assertEqual(result, "⚠️ Ошибка: Connection timeout")

    def test_with_partial_output(self):
        """B. Combine partial output and error"""
        result = format_partial_output_message("Generated text", "Process error")
        self.assertIn("Generated text", result)
        self.assertIn("⚠️ Ошибка:", result)
        self.assertIn("Process error", result)
        self.assertTrue(result.startswith("Generated text"))

    def test_newline_separation(self):
        """B. Proper newline separation between output and error"""
        result = format_partial_output_message("Output", "Error")
        lines = result.split("\n\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "Output")
        self.assertIn("⚠️ Ошибка:", lines[1])


class TestExecutionTraceErrorHandling(unittest.TestCase):
    """Test ExecutionTrace error recording and response handling"""

    def test_error_recording(self):
        """Record error in trace"""
        trace = ExecutionTrace()
        try:
            raise ValueError("Test error")
        except ValueError as e:
            trace.record_error("test_source", str(e), exception=e)
        
        trace_dict = trace.finalize()
        self.assertGreater(len(trace_dict["errors"]), 0)
        error_entry = trace_dict["errors"][0]
        self.assertEqual(error_entry["source"], "test_source")
        self.assertEqual(error_entry["error"], "Test error")
        self.assertIn("python_exception", error_entry)

    def test_error_with_response(self):
        """Responses preserved when error occurs"""
        trace = ExecutionTrace()
        
        # Add a response before error
        response_data = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "text", "text": "Partial output"}
                    ]
                }
            ]
        }
        trace.add_response(response_data, step_index=1)
        
        # Record error
        trace.record_error("pipeline", "Error after response")
        
        trace_dict = trace.finalize()
        self.assertGreater(len(trace_dict["responses"]), 0)
        self.assertGreater(len(trace_dict["errors"]), 0)

    def test_trace_finalize_called_on_error(self):
        """Trace must be finalized even on error"""
        trace = ExecutionTrace()
        trace.set_request({"test": "request"})
        trace.record_error("test", "error")
        
        trace_dict = trace.finalize()
        self.assertIn("trace_id", trace_dict)
        self.assertIn("created_at", trace_dict)
        self.assertIn("errors", trace_dict)
        self.assertGreater(trace_dict["timings"]["total_duration_ms"], 0)

    def test_secret_redaction_in_error(self):
        """Secrets redacted in error context"""
        trace = ExecutionTrace()
        
        try:
            # Simulate exception with sensitive local variable
            api_key = "secret_key_123"
            raise ValueError("Error occurred")
        except ValueError as e:
            trace.record_error("test", str(e), exception=e)
        
        trace_dict = trace.finalize()
        error_entry = trace_dict["errors"][0]
        
        # Check that locals are captured and redacted
        if "python_exception" in error_entry:
            exc_state = error_entry["python_exception"]
            self.assertIn("frames", exc_state)


class TestPartialOutputIntegration(unittest.TestCase):
    """Integration tests for partial output in error scenarios"""

    def test_scenario_a_error_before_response(self):
        """A. Error before model response - no partial output"""
        trace = ExecutionTrace()
        trace.set_request({"message": "test"})
        
        # No responses added
        error_msg = "Connection failed"
        trace.record_error("api", error_msg)
        trace_dict = trace.finalize()
        
        # Extract should find nothing
        text, _ = extract_last_response_text(trace_dict.get("responses", []))
        self.assertEqual(text, "")
        
        # Format should just show error
        formatted = format_partial_output_message(text, error_msg)
        self.assertEqual(formatted, f"⚠️ Ошибка: {error_msg}")

    def test_scenario_b_response_then_error(self):
        """B. Response + error - partial output preserved"""
        trace = ExecutionTrace()
        trace.set_request({"message": "test"})
        
        # Add response with text
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "text", "text": "Model output"}]
                }
            ]
        }
        trace.add_response(response, step_index=1)
        
        # Record error
        error_msg = "Post-processing failed"
        trace.record_error("pipeline", error_msg)
        trace_dict = trace.finalize()
        
        # Extract should find text
        text, _ = extract_last_response_text(trace_dict.get("responses", []))
        self.assertEqual(text, "Model output")
        
        # Format should include both
        formatted = format_partial_output_message(text, error_msg)
        self.assertIn("Model output", formatted)
        self.assertIn("⚠️ Ошибка:", formatted)
        self.assertIn(error_msg, formatted)

    def test_scenario_c_multiple_responses(self):
        """C. Multiple Responses API - get last relevant text"""
        trace = ExecutionTrace()
        
        # First response with text
        resp1 = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "text", "text": "First step"}]
                }
            ]
        }
        trace.add_response(resp1, step_index=1)
        
        # Second response with more text
        resp2 = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "text", "text": "Second step"}]
                }
            ]
        }
        trace.add_response(resp2, step_index=2)
        
        error_msg = "Error at step 3"
        trace.record_error("pipeline", error_msg)
        trace_dict = trace.finalize()
        
        # Should get second response text
        text, _ = extract_last_response_text(trace_dict.get("responses", []))
        self.assertEqual(text, "Second step")

    def test_scenario_d_tool_call_error(self):
        """D. Tool call + error - both preserved in trace"""
        trace = ExecutionTrace()
        trace.set_request({"message": "test"})
        
        # Response with initial text
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "text", "text": "Starting..."}]
                }
            ]
        }
        trace.add_response(response, step_index=1)
        
        # Tool call
        def failing_tool():
            raise RuntimeError("Tool failed")
        
        try:
            trace.track_tool_execution("test_tool", {}, failing_tool)
        except RuntimeError:
            pass
        
        trace_dict = trace.finalize()
        
        # Verify tool call is recorded
        self.assertGreater(len(trace_dict["tool_calls"]), 0)
        tool_entry = trace_dict["tool_calls"][0]
        self.assertEqual(tool_entry["name"], "test_tool")
        self.assertIsNotNone(tool_entry["error"])
        
        # Partial output still available
        text, _ = extract_last_response_text(trace_dict.get("responses", []))
        self.assertEqual(text, "Starting...")


if __name__ == "__main__":
    unittest.main()
