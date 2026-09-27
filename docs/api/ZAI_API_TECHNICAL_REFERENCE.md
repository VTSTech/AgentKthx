# Z.AI API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**  
> **Generated from**: https://docs.z.ai  
> **Last Updated**: 2026-09-19 3:00:48 PM
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [Request/Response Structure](#requestresponse-structure)
3. [Model Family Specifications](#model-family-specifications)
4. [Function Calling Implementation](#function-calling-implementation)
5. [Streaming & Real-time Features](#streaming--real-time-features)
6. [Error Codes & Recovery](#error-codes--recovery)
7. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
8. [Multimodal Content Handling](#multimodal-content-handling)
9. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
10. [Troubleshooting Matrix](#troubleshooting-matrix)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production
BASE_URL = "https://api.z.ai/api"

# API Endpoint
CHAT_COMPLETIONS = "/paas/v4/chat/completions"
MODELS = "/paas/v4/models"  # Available on some plans
```

### Authentication Headers

```python
headers = {
    "Content-Type": "application/json",
    "Accept-Language": "en-US,en",  # Optional but recommended
    "Authorization": "Bearer YOUR_API_KEY"
}
```

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: 4MB
- **Timeout**: 120 seconds (configurable)

---

## Request/Response Structure

### Complete Request Schema

```json
{
    "model": "glm-5.3",
    "messages": [
        {
            "role": "system|user|assistant|tool",
            "content": "string|array",  # Text or multimodal array
            "tool_calls": "array",     # For assistant messages
            "tool_call_id": "string"   # For tool messages
        }
    ],
    "temperature": 0.7,
    "top_p": 0.95,
    "max_tokens": 2048,
    "stream": false,
    "do_sample": true,
    "thinking": {
        "type": "enabled",
        "clear_thinking": true
    },
    "reasoning_effort": "max",
    "tools": [
        {
            "type": "function|web_search|retrieval",
            "function": {
                "name": "string",
                "description": "string",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": []
                }
            }
        }
    ],
    "tool_choice": "auto",
    "tool_stream": false,
    "response_format": {"type": "text|json_object"},
    "stop": ["###"],
    "request_id": "string",
    "user_id": "string"
}
```

### Response Schema Details

```json
{
    "id": "chatcmpl-123456",
    "request_id": "req_123456789",
    "created": 1700000000,
    "model": "glm-5.3",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "reasoning_content": "Chain of thought reasoning (GLM-4.5+)",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "function_name",
                            "arguments": "{\"param1\": \"value1\"}"
                        }
                    }
                ]
            },
            "finish_reason": "stop|tool_calls|length|sensitive|model_context_window_exceeded|network_error"
        }
    ],
    "usage": {
        "prompt_tokens": 20,
        "completion_tokens": 50,
        "total_tokens": 70,
        "prompt_tokens_details": {
            "cached_tokens": 0
        }
    },
    "web_search": [
        {
            "title": "string",
            "content": "string",
            "link": "string",
            "media": "string",
            "icon": "string",
            "refer": "string",
            "publish_date": "string"
        }
    ]
}
```

---

## Model Family Specifications

### Model Metadata Structure

```python
MODEL_CONFIGS = {
    "glm-5.3": {
        "context_length": 131072,
        "max_output_tokens": 131072,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 1.0,
        "top_p_default": 0.95,
        "family": "glm-5",
        "tier": "flagship"
    },
    "glm-5.3-flash": {
        "context_length": 131072,
        "max_output_tokens": 131072,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": True,
        "temperature_default": 1.0,
        "top_p_default": 0.95,
        "family": "glm-5",
        "tier": "flash"
    },
    "glm-4.5": {
        "context_length": 131072,  # Display: 128K
        "max_output_tokens": 98304,  # Actual max
        "supports_thinking": True,
        "supports_reasoning_effort": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.6,
        "top_p_default": 0.95,
        "family": "glm-4",
        "tier": "standard"
    }
}
```

### Model Detection & Auto-configuration

```python
def detect_model_family(model_name: str) -> dict:
    """Detect model capabilities from name"""
    if model_name.startswith("glm-5."):
        return {
            "family": "glm-5",
            "supports_native_tools": True,
            "supports_thinking": True,
            "reasoning_levels": ["max", "high", "low"]
        }
    elif model_name.startswith("glm-4."):
        return {
            "family": "glm-4", 
            "supports_native_tools": True,
            "supports_thinking": True,
            "reasoning_levels": []
        }
    else:
        return {
            "family": "unknown",
            "supports_native_tools": False,
            "supports_thinking": False
        }
```

---

## Function Calling Implementation

### Tool Schema Requirements

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather information for specified city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name (e.g., Beijing, Shanghai)",
                    "examples": ["Beijing", "Shanghai"]
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "default": "celsius"
                }
            },
            "required": ["city"]
        }
    }
}
```

### Tool Flow Implementation

```python
class ZaiToolHandler:
    def __init__(self, api_key: str):
        self.api_key = api_key
        
    def convert_to_zai_tools(self, agent_tools: list) -> list:
        """Convert AgentKthx tools to ZAI format"""
        zai_tools = []
        for tool in agent_tools:
            if hasattr(tool, 'to_openai_schema'):
                # Tool has OpenAI-compatible schema
                zai_tools.append({
                    "type": "function",
                    "function": tool.to_openai_schema()
                })
            else:
                # Convert custom tool format
                zai_tools.append(self._convert_custom_tool(tool))
        return zai_tools
    
    def handle_tool_calls(self, response: dict, tools: list) -> list:
        """Extract and handle tool calls from response"""
        tool_calls = []
        
        if 'choices' in response and len(response['choices']) > 0:
            choice = response['choices'][0]
            if 'message' in choice and 'tool_calls' in choice['message']:
                for tool_call in choice['message']['tool_calls']:
                    tool_calls.append({
                        'id': tool_call['id'],
                        'type': tool_call['type'],
                        'function': {
                            'name': tool_call['function']['name'],
                            'arguments': json.loads(tool_call['function']['arguments'])
                        }
                    })
        
        return tool_calls
    
    def execute_tool_call(self, tool_call: dict) -> dict:
        """Execute a single tool call"""
        function_name = tool_call['function']['name']
        arguments = tool_call['function']['arguments']
        
        # Find matching tool
        tool = self._find_tool_by_name(function_name)
        if not tool:
            return {
                "role": "tool",
                "content": f"Error: Tool '{function_name}' not found",
                "tool_call_id": tool_call['id']
            }
        
        try:
            result = tool.execute(**arguments)
            return {
                "role": "tool", 
                "content": json.dumps(result, ensure_ascii=False),
                "tool_call_id": tool_call['id']
            }
        except Exception as e:
            return {
                "role": "tool",
                "content": json.dumps({"error": str(e)}, ensure_ascii=False),
                "tool_call_id": tool_call['id']
            }
```

---

## Streaming & Real-time Features

### Streaming Response Format

```json
# First chunk
data: {
    "id": "chatcmpl-123456",
    "object": "chat.completion.chunk",
    "created": 1700000000,
    "model": "glm-5.3",
    "choices": [{
        "index": 0,
        "delta": {
            "role": "assistant",
            "content": "Hello"
        },
        "finish_reason": null
    }]
}

# Middle chunk
data: {
    "choices": [{
        "index": 0,
        "delta": {
            "content": ", how can I help"
        },
        "finish_reason": null
    }]
}

# Final chunk
data: {
    "choices": [{
        "index": 0,
        "delta": {},
        "finish_reason": "stop"
    }]
}
data: [DONE]
```

### Streaming Implementation

```python
import json
import sseclient

class ZaiStreamHandler:
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    def create_streaming_request(self, messages, **kwargs):
        """Create streaming request with proper headers"""
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        data = {
            "model": kwargs.get("model", "glm-5.3"),
            "messages": messages,
            "stream": True,
            **kwargs
        }
        
        return requests.post(
            f"{BASE_URL}/paas/v4/chat/completions",
            headers=headers,
            json=data,
            stream=True
        )
    
    def handle_stream_response(self, response, callbacks=None):
        """Handle streaming response with callbacks"""
        callbacks = callbacks or {}
        
        try:
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data = json.loads(line[6:])
                        self._process_stream_chunk(data, callbacks)
        except Exception as e:
            if 'callbacks' in callbacks and 'error' in callbacks:
                callbacks['error'](str(e))
    
    def _process_stream_chunk(self, data, callbacks):
        """Process individual streaming chunk"""
        if 'choices' in data and len(data['choices']) > 0:
            chunk = data['choices'][0]
            
            if 'delta' in chunk:
                content = chunk['delta'].get('content', '')
                
                if 'content_callback' in callbacks:
                    callbacks['content'](content)
                
                if 'tool_calls' in chunk['delta']:
                    # Handle streaming tool calls
                    if 'tool_callback' in callbacks:
                        callbacks['tool_calls'](chunk['delta']['tool_calls'])
            
            if 'finish_reason' in chunk and chunk['finish_reason']:
                if 'done_callback' in callbacks:
                    callbacks['done'](chunk['finish_reason'])
```

---

## Error Codes & Recovery

### Error Response Structure

```json
{
    "code": 400,
    "message": "Invalid request format"
}
```

### Comprehensive Error Handling

```python
class ZaiErrorHandler:
    ERROR_CODES = {
        400: {
            "message": "Bad Request",
            "recoverable": False,
            "actions": ["Check request format", "Validate parameters"]
        },
        401: {
            "message": "Invalid API Key",
            "recoverable": False,
            "actions": ["Verify API key", "Check key status"]
        },
        403: {
            "message": "Permission Denied",
            "recoverable": False,
            "actions": ["Check account status", "Verify subscription"]
        },
        429: {
            "message": "Rate Limit Exceeded",
            "recoverable": True,
            "retry_after": "Retry-After header",
            "actions": ["Implement exponential backoff", "Check rate limits"]
        },
        500: {
            "message": "Internal Server Error",
            "recoverable": True,
            "retry_after": 5,
            "actions": ["Retry with backoff", "Check service status"]
        },
        503: {
            "message": "Service Unavailable",
            "recoverable": True,
            "retry_after": 30,
            "actions": ["Retry with longer backoff", "Check service status"]
        }
    }
    
    def handle_error(self, response: requests.Response) -> dict:
        """Handle API errors with recovery suggestions"""
        try:
            error_data = response.json()
            code = error_data.get('code', response.status_code)
        except:
            code = response.status_code
        
        error_info = self.ERROR_CODES.get(code, {
            "message": "Unknown Error",
            "recoverable": False,
            "actions": ["Check logs", "Contact support"]
        })
        
        return {
            "code": code,
            "message": error_info["message"],
            "recoverable": error_info["recoverable"],
            "retry_after": error_info.get("retry_after", 0),
            "actions": error_info["actions"],
            "status_code": response.status_code
        }
    
    def should_retry(self, error: dict, attempt: int, max_attempts: int) -> bool:
        """Determine if request should be retried"""
        if not error["recoverable"]:
            return False
        
        if attempt >= max_attempts:
            return False
        
        # Rate limit errors
        if error["code"] == 429:
            return True
        
        # Server errors
        if error["code"] in [500, 503, 504]:
            return True
        
        return False
```

### Retry Logic Implementation

```python
class ZaiRetryHandler:
    def __init__(self, max_attempts=3, base_delay=1):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.error_handler = ZaiErrorHandler()
    
    def execute_with_retry(self, request_func, **kwargs):
        """Execute request with retry logic"""
        last_error = None
        
        for attempt in range(self.max_attempts):
            try:
                response = request_func(**kwargs)
                
                if response.status_code == 200:
                    return response
                
                error = self.error_handler.handle_error(response)
                if not self.error_handler.should_retry(error, attempt, self.max_attempts):
                    return response
                
                delay = self.calculate_delay(error, attempt)
                time.sleep(delay)
                last_error = error
                
            except Exception as e:
                last_error = {
                    "code": "NETWORK_ERROR",
                    "message": str(e),
                    "recoverable": True
                }
                
                if not self.error_handler.should_retry(last_error, attempt, self.max_attempts):
                    raise
        
        # Return last response if we have one
        if 'response' in locals():
            return response
        else:
            raise Exception(f"Max retries exceeded. Last error: {last_error}")
    
    def calculate_delay(self, error: dict, attempt: int) -> float:
        """Calculate exponential backoff delay"""
        base_delay = error.get("retry_after", self.base_delay)
        return base_delay * (2 ** attempt) + random.uniform(0, 1)
```

---

## Rate Limiting & Concurrency

### Rate Limit Headers

```python
RATE_LIMIT_HEADERS = {
    'x-ratelimit-limit': '100',           # Requests per window
    'x-ratelimit-remaining': '85',       # Remaining requests
    'x-ratelimit-reset': '1700000000',   # Unix timestamp for reset
    'retry-after': '60'                  # Seconds until rate limit resets
}
```

### Rate Limiting Implementation

```python
class ZaiRateLimiter:
    def __init__(self, requests_per_minute=60):
        self.requests_per_minute = requests_per_minute
        self.requests = []
        self.lock = threading.Lock()
    
    def can_make_request(self) -> bool:
        """Check if request can be made based on rate limits"""
        with self.lock:
            now = time.time()
            
            # Remove requests older than 1 minute
            self.requests = [req_time for req_time in self.requests if now - req_time < 60]
            
            # Check if we can make more requests
            return len(self.requests) < self.requests_per_minute
    
    def record_request(self):
        """Record that a request was made"""
        with self.lock:
            self.requests.append(time.time())
    
    def get_wait_time(self) -> float:
        """Get how long to wait before next request"""
        with self.lock:
            if len(self.requests) < self.requests_per_minute:
                return 0
            
            oldest_request = min(self.requests)
            return max(0, 60 - (time.time() - oldest_request))
```

### Concurrency Management

```python
class ZaiConcurrencyManager:
    def __init__(self, max_concurrent=5):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_requests = 0
    
    async def execute_request(self, request_func, *args, **kwargs):
        """Execute request with concurrency control"""
        async with self.semaphore:
            self.active_requests += 1
            try:
                return await request_func(*args, **kwargs)
            finally:
                self.active_requests -= 1
    
    def get_status(self) -> dict:
        """Get current concurrency status"""
        return {
            "active_requests": self.active_requests,
            "max_concurrent": self.semaphore._value,
            "available_slots": self.semaphore._value - self.active_requests
        }
```

---

## Multimodal Content Handling

### Content Types and Validation

```python
class ZaiMultimodalHandler:
    CONTENT_TYPE_VALIDATION = {
        "text": {
            "max_length": 131072,
            "allowed_types": [str]
        },
        "image_url": {
            "max_size_mb": 5,
            "max_pixels": 6000 * 6000,
            "allowed_formats": ["jpg", "jpeg", "png"],
            "max_images": 150  # GLM-5.3-FlashX
        },
        "video_url": {
            "max_size_mb": 200,
            "max_videos": 2,  # GLM-5.3-FlashX
            "allowed_formats": ["mp4", "mkv", "mov"]
        },
        "file": {
            "max_size_mb": 50,
            "max_files": 50,
            "allowed_formats": ["pdf", "txt", "doc", "jsonl", "xlsx", "pptx"]
        }
    }
    
    def validate_content(self, content: list) -> dict:
        """Validate multimodal content structure"""
        errors = []
        
        for i, item in enumerate(content):
            if not isinstance(item, dict):
                errors.append(f"Item {i}: Must be a dictionary")
                continue
            
            content_type = item.get("type")
            if content_type not in self.CONTENT_TYPE_VALIDATION:
                errors.append(f"Item {i}: Invalid content type '{content_type}'")
                continue
            
            validator = self.CONTENT_TYPE_VALIDATION[content_type]
            
            if content_type == "text":
                if not isinstance(item.get("text"), str):
                    errors.append(f"Item {i}: Text content must be a string")
                
                if len(item.get("text", "")) > validator["max_length"]:
                    errors.append(f"Item {i}: Text too long ({validator['max_length']} max)")
            
            elif content_type == "image_url":
                image_url = item.get("image_url", {}).get("url")
                if not image_url:
                    errors.append(f"Item {i}: Missing image URL")
                
                # Validate URL format
                if not self._is_valid_url(image_url):
                    errors.append(f"Item {i}: Invalid image URL")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
    
    def format_multimodal_message(self, role: str, content: list) -> dict:
        """Format multimodal message for ZAI API"""
        validation = self.validate_content(content)
        if not validation["valid"]:
            raise ValueError(f"Invalid content: {validation['errors']}")
        
        return {
            "role": role,
            "content": content
        }
```

### Multimodal Request Builder

```python
class ZaiMultimodalRequestBuilder:
    def __init__(self, model: str):
        self.model = model
        self.handler = ZaiMultimodalHandler()
    
    def build_vision_request(self, image_urls: list, text_prompt: str) -> dict:
        """Build vision model request"""
        content = []
        
        # Add images
        for i, url in enumerate(image_urls):
            content.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        
        # Add text prompt
        content.append({
            "type": "text",
            "text": text_prompt
        })
        
        return {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": content
            }]
        }
    
    def build_file_analysis_request(self, file_urls: list, question: str) -> dict:
        """Build file analysis request"""
        content = []
        
        # Add files
        for file_url in file_urls:
            content.append({
                "type": "file",
                "file": {
                    "file_url": file_url
                }
            })
        
        # Add question
        content.append({
            "type": "text",
            "text": question
        })
        
        return {
            "model": self.model,
            "messages": [{
                "role": "user", 
                "content": content
            }]
        }
```

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

```python
class ZaiBackend(BaseBackend):
    def __init__(self, api_key: str, base_url: str = "https://api.z.ai/api"):
        super().__init__(config=config, base_url=base_url)
        self.api_key = api_key
        self.model_cache = {}
        self.error_handler = ZaiErrorHandler()
        self.retry_handler = ZaiRetryHandler()
        self.rate_limiter = ZaiRateLimiter()
    
    async def generate(self, model: str, messages: list, **kwargs) -> dict:
        """Main generation method"""
        # Rate limiting check
        if not self.rate_limiter.can_make_request():
            wait_time = self.rate_limiter.get_wait_time()
            await asyncio.sleep(wait_time)
        
        # Build request
        request = self._build_request(model, messages, **kwargs)
        
        # Execute with retry
        response = self.retry_handler.execute_with_retry(
            self._make_request,
            request=request
        )
        
        return self._parse_response(response)
    
    def _build_request(self, model: str, messages: list, **kwargs) -> dict:
        """Build ZAI API request"""
        request = {
            "model": model,
            "messages": messages,
            "do_sample": kwargs.get("do_sample", True),
            "temperature": kwargs.get("temperature"),
            "top_p": kwargs.get("top_p"),
            "max_tokens": kwargs.get("num_predict"),
            "stream": kwargs.get("stream", False),
            "request_id": kwargs.get("request_id"),
            "user_id": kwargs.get("user_id")
        }
        
        # Add tools if available
        if "tools" in kwargs:
            request["tools"] = kwargs["tools"]
            request["tool_choice"] = kwargs.get("tool_choice", "auto")
        
        # Add thinking parameters
        if "thinking" in kwargs:
            request["thinking"] = kwargs["thinking"]
        
        return request
```

### 2. Model Auto-detection

```python
def auto_detect_model_config(model_name: str) -> dict:
    """Auto-detect model configuration from name"""
    config = {
        "max_tokens": 4096,  # Default fallback
        "temperature": 0.7,
        "top_p": 0.95,
        "supports_streaming": True,
        "supports_tools": True,
        "supports_thinking": False
    }
    
    # GLM-5 series
    if model_name.startswith("glm-5."):
        config.update({
            "max_tokens": 131072,
            "supports_thinking": True
        })
        
        if "flash" in model_name.lower():
            config.update({
                "supports_multimodal": True,
                "max_images": 150 if "flashx" in model_name else 10
            })
    
    # GLM-4 series
    elif model_name.startswith("glm-4."):
        config.update({
            "max_tokens": 98304,
            "supports_thinking": True
        })
        
        if "flash" in model_name.lower():
            config.update({
                "supports_multimodal": True
            })
    
    return config
```

### 3. Tool Schema Compatibility

```python
def convert_agentkthx_tools_to_zai(tools: list) -> list:
    """Convert AgentKthx tool schema to ZAI format"""
    zai_tools = []
    
    for tool in tools:
        if hasattr(tool, 'to_openai_schema'):
            # Already OpenAI compatible
            zai_tools.append({
                "type": "function",
                "function": tool.to_openai_schema()
            })
        else:
            # Convert AgentKthx format
            zai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            })
    
    return zai_tools
```

### 4. Context Management

```python
class ZaiContextManager:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.config = auto_detect_model_config(model_name)
        self.message_window = []
        self.max_context = self.config["max_tokens"]
    
    def add_message(self, role: str, content: str):
        """Add message while managing context"""
        message = {"role": role, "content": content}
        
        # Calculate token count
        tokens = self._count_tokens(content)
        
        # Truncate if needed
        while len(self.message_window) > 0 and (
            self._get_total_tokens() + tokens > self.max_context
        ):
            removed = self.message_window.pop(0)
            tokens -= self._count_tokens(removed["content"])
        
        self.message_window.append(message)
    
    def get_context(self) -> list:
        """Get current context window"""
        return self.message_window.copy()
    
    def _count_tokens(self, text: str) -> int:
        """Approximate token count"""
        return len(text) // 4  # Rough approximation
    
    def _get_total_tokens(self) -> int:
        """Calculate total tokens in context"""
        return sum(self._count_tokens(msg["content"]) for msg in self.message_window)
```

### 5. Integration Testing

```python
class ZaiIntegrationTests:
    def test_basic_chat(self):
        """Test basic chat functionality"""
        backend = ZaiBackend(api_key="test-key")
        messages = [
            {"role": "user", "content": "Hello, introduce yourself"}
        ]
        
        response = backend.generate(
            model="glm-5.3",
            messages=messages,
            temperature=0.7
        )
        
        assert "choices" in response
        assert len(response["choices"]) > 0
        assert "content" in response["choices"][0]["message"]
    
    def test_function_calling(self):
        """Test function calling capability"""
        backend = ZaiBackend(api_key="test-key")
        
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"}
                    },
                    "required": ["city"]
                }
            }
        }]
        
        messages = [
            {"role": "user", "content": "Weather in Beijing?"}
        ]
        
        response = backend.generate(
            model="glm-5.3",
            messages=messages,
            tools=tools
        )
        
        # Should either respond directly or request tool calls
        assert "choices" in response
        choice = response["choices"][0]
        assert choice["message"]["role"] == "assistant"
    
    def test_error_handling(self):
        """Test error handling and recovery"""
        backend = ZaiBackend(api_key="invalid-key")
        
        try:
            response = backend.generate(
                model="glm-5.3",
                messages=[{"role": "user", "content": "test"}]
            )
            assert False, "Should have raised an error"
        except Exception as e:
            assert "error" in str(e).lower()
```

---

## Troubleshooting Matrix

### Issue Detection & Solutions

| Symptom | Possible Cause | Solution | Priority |
|---------|---------------|----------|----------|
| 429 errors | Rate limiting | Implement backoff, check limits | High |
| 401 errors | Invalid API key | Verify key, check status | High |
| Empty responses | Content filtering | Check prompt sensitivity | Medium |
| Tool not working | Schema mismatch | Validate tool schema | Medium |
| High latency | Server load | Use streaming, retry with backoff | Low |
| Context overflow | Too long messages | Implement context truncation | Medium |

### Debug Mode Implementation

```python
class ZaiDebugHandler:
    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode
        self.debug_log = []
    
    def log_request(self, request: dict):
        """Log outgoing request for debugging"""
        if self.debug_mode:
            self.debug_log.append({
                "timestamp": time.time(),
                "type": "request",
                "model": request.get("model"),
                "messages_count": len(request.get("messages", [])),
                "tools_count": len(request.get("tools", [])),
                "message_preview": self._preview_content(request)
            })
            print(f"[DEBUG] Sending request to {request.get('model')}")
    
    def log_response(self, response: dict):
        """Log incoming response for debugging"""
        if self.debug_mode:
            self.debug_log.append({
                "timestamp": time.time(),
                "type": "response",
                "id": response.get("id"),
                "model": response.get("model"),
                "tokens": response.get("usage", {}).get("total_tokens"),
                "finish_reason": response.get("choices", [{}])[0].get("finish_reason")
            })
            print(f"[DEBUG] Response received: {response.get('id')}")
    
    def _preview_content(self, request: dict) -> str:
        """Preview request content for logging"""
        messages = request.get("messages", [])
        preview = []
        for msg in messages[-3:]:  # Last 3 messages
            role = msg.get("role")
            content = str(msg.get("content", ""))[:100]
            preview.append(f"{role}: {content}...")
        return " | ".join(preview)
    
    def get_debug_summary(self) -> dict:
        """Get debug summary for troubleshooting"""
        requests = [log for log in self.debug_log if log["type"] == "request"]
        responses = [log for log in self.debug_log if log["type"] == "response"]
        
        return {
            "total_requests": len(requests),
            "total_responses": len(responses),
            "success_rate": len(responses) / max(len(requests), 1),
            "avg_tokens": self._avg_tokens(responses),
            "recent_activity": self.debug_log[-5:] if self.debug_log else []
        }
    
    def _avg_tokens(self, responses: list) -> float:
        """Calculate average tokens per response"""
        if not responses:
            return 0
        return sum(r.get("tokens", 0) for r in responses) / len(responses)
```

### Performance Monitoring

```python
class ZaiPerformanceMonitor:
    def __init__(self):
        self.metrics = {
            "request_count": 0,
            "total_tokens": 0,
            "total_time": 0,
            "error_count": 0,
            "start_time": time.time()
        }
    
    def record_request(self, tokens: int, duration: float, success: bool = True):
        """Record request performance metrics"""
        self.metrics["request_count"] += 1
        self.metrics["total_tokens"] += tokens
        self.metrics["total_time"] += duration
        
        if not success:
            self.metrics["error_count"] += 1
    
    def get_performance_stats(self) -> dict:
        """Get performance statistics"""
        uptime = time.time() - self.metrics["start_time"]
        return {
            "requests_per_second": self.metrics["request_count"] / uptime,
            "tokens_per_second": self.metrics["total_tokens"] / uptime,
            "average_tokens_per_request": self.metrics["total_tokens"] / max(self.metrics["request_count"], 1),
            "average_response_time": self.metrics["total_time"] / max(self.metrics["request_count"], 1),
            "error_rate": self.metrics["error_count"] / max(self.metrics["request_count"], 1),
            "uptime_seconds": uptime
        }
```

---

This technical reference provides the detailed implementation information needed to improve AgentKthx's ZAI backend integration, focusing on practical implementation details rather than basic API documentation.