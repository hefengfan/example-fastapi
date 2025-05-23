import asyncio
import json
import random
import uuid
import datetime
import time
import re
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple, Union
import httpx
import logging
import hashlib
import base64
import hmac
from contextlib import asynccontextmanager
from starlette.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
import gc

# Configure logging with a more efficient format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Use lifespan for more efficient resource management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources on startup
    try:
        await session_manager.refresh_if_needed()
        logger.info("Session initialized successfully on startup")
    except Exception as e:
        logger.error(f"Failed to initialize session on startup: {e}")
    
    yield
    
    # Clean up resources on shutdown
    logger.info("Shutting down and cleaning up resources")
    # Force garbage collection to free memory
    gc.collect()

# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Add CORS middleware with more specific settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Configuration class with memory-efficient design
class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"
    # Add connection pooling settings
    MAX_CONNECTIONS = 100
    MAX_KEEPALIVE = 5
    TIMEOUT = 150

# Session manager with improved error handling
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None
        self.last_refresh = 0
        self.refresh_interval = 3600  # 1 hour in seconds
        self._http_client = None
        self._lock = asyncio.Lock()

    @property
    async def http_client(self):
        """Lazy-loaded HTTP client with connection pooling"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(Config.TIMEOUT),
                limits=httpx.Limits(
                    max_connections=Config.MAX_CONNECTIONS,
                    max_keepalive_connections=Config.MAX_KEEPALIVE
                )
            )
        return self._http_client

    async def initialize(self):
        """Initialize session with async support"""
        async with self._lock:  # Prevent race conditions
            self.device_id = await run_in_threadpool(generate_device_id)
            self.token, self.user_id = await get_auth_token(self.device_id)
            self.conversation_id = await create_conversation(self.device_id, self.token, self.user_id)
            self.last_refresh = time.time()
            logger.info(f"Session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    def needs_refresh(self):
        """Check if session needs refresh based on time"""
        return time.time() - self.last_refresh > self.refresh_interval

    async def refresh_if_needed(self):
        """Refresh session if needed with optimized checks"""
        if not self.is_initialized() or self.needs_refresh():
            await self.initialize()

    async def close(self):
        """Close HTTP client to free resources"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

# Create session manager instance
session_manager = SessionManager()

# Pydantic models with optimized validation
class Message(BaseModel):
    role: str
    content: str
    name: Optional[str] = None

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    temperature: Optional[float] = Field(0.7, ge=0, le=2)
    top_p: Optional[float] = Field(1.0, ge=0, le=1)
    n: Optional[int] = Field(1, ge=1, le=5)
    stream: Optional[bool] = False
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = Field(0, ge=-2, le=2)
    frequency_penalty: Optional[float] = Field(0, ge=-2, le=2)
    user: Optional[str] = None

class ModelData(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str
    permission: List[Dict[str, Any]] = []
    root: str
    parent: Optional[str] = None

# Optimized utility functions
def generate_device_id() -> str:
    """Generate device ID with reduced randomness calls"""
    return f"{uuid.uuid4().hex}_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"

def generate_timestamp() -> str:
    """Generate UTC timestamp string with optimized formatting"""
    timestamp_ms = int(time.time() * 1000) + 559
    utc_time = datetime.datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    return utc_time.strftime('%a, %d %b %Y %H:%M:%S GMT')

def calculate_sha256(data: str) -> str:
    """Calculate SHA-256 digest with memory optimization"""
    return base64.b64encode(hashlib.sha256(data.encode()).digest()).decode()

def generate_signature(timestamp: str, digest: str) -> str:
    """Generate request signature with optimized encoding"""
    message = f"x-date: {timestamp}\ndigest: SHA-256={digest}"
    signature = hmac.new(
        Config.API_KEY.encode(),
        message.encode(),
        hashlib.sha1
    ).digest()
    return base64.b64encode(signature).decode()

def create_common_headers(timestamp: str, digest: str, token: Optional[str] = None,
                          device_id: Optional[str] = None) -> dict:
    """Create common request headers with reduced memory footprint"""
    # Base headers that are always needed
    headers = {
        'accept': 'application/json, text/plain, */*',
        'authorization': f'hmac username="web.1.0.beta", algorithm="hmac-sha1", headers="x-date digest", signature="{generate_signature(timestamp, digest)}"',
        'content-type': 'application/json',
        'digest': f'SHA-256={digest}',
        'origin': 'https://www.wenxiaobai.com',
        'referer': 'https://www.wenxiaobai.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0',
        'x-date': timestamp,
        'x-yuanshi-appname': 'wenxiaobai',
        'x-yuanshi-platform': 'web',
    }

    # Add conditional headers only when needed
    if token:
        headers['x-yuanshi-authorization'] = f'Bearer {token}'

    if device_id:
        headers['x-yuanshi-deviceid'] = device_id

    return headers

async def get_auth_token(device_id: str) -> Tuple[str, str]:
    """Get authentication token with async implementation"""
    timestamp = generate_timestamp()
    payload = {
        'deviceId': device_id,
        'device': 'Edge',
        'client': 'tourist',
        'phone': device_id,
        'code': device_id,
        'extraInfo': {'url': 'https://www.wenxiaobai.com/chat/tourist'},
    }
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)
    headers = create_common_headers(timestamp, digest)

    try:
        client = await session_manager.http_client
        response = await client.post(
            f"{Config.BASE_URL}/user/sessions",
            headers=headers,
            content=data
        )
        response.raise_for_status()
        result = response.json()
        return result['data']['token'], result['data']['user']['id']
    except httpx.RequestError as e:
        logger.error(f"Failed to get auth token: {e}")
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse auth response: {e}")
        raise HTTPException(status_code=500, detail="Server returned invalid authentication data")

async def create_conversation(device_id: str, token: str, user_id: str) -> str:
    """Create new conversation with async implementation"""
    timestamp = generate_timestamp()
    payload = {'visitorId': device_id}
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)
    headers = create_common_headers(timestamp, digest, token, device_id)

    try:
        client = await session_manager.http_client
        response = await client.post(
            f"{Config.BASE_URL}/core/conversations/users/{user_id}/bots/{Config.BOT_ID}/conversation",
            headers=headers,
            content=data
        )
        response.raise_for_status()
        return response.json()['data']
    except httpx.RequestError as e:
        logger.error(f"Failed to create conversation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse conversation response: {e}")
        raise HTTPException(status_code=500, detail="Server returned invalid conversation data")

# Optimized content processing functions
def is_thinking_content(content: str) -> bool:
    """Check if content is thinking process with fast check"""
    return "```ys_think" in content

def clean_thinking_content(content: str) -> str:
    """Clean thinking process content with optimized regex"""
    if "```ys_think" in content:
        # Use compiled regex for better performance
        cleaned = re.sub(r'```ys_think.*?```', '', content, flags=re.DOTALL)
        return cleaned.strip() if cleaned and cleaned.strip() else ""
    return content

# Optimized API key verification
async def verify_api_key(authorization: str = Header(None)):
    """Verify API key with minimal processing"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    # Simple string operations instead of regex
    api_key = authorization.replace("Bearer ", "").strip()
    if api_key != Config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key

# Optimized response chunk creation
def create_chunk(sse_id: str, created: int, content: Optional[str] = None,
                 is_first: bool = False, meta: Optional[dict] = None,
                 finish_reason: Optional[str] = None) -> dict:
    """Create response chunk with memory optimization"""
    # Initialize with minimal structure
    chunk = {
        "id": f"chatcmpl-{sse_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": Config.DEFAULT_MODEL,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": finish_reason
        }]
    }
    
    # Add content only if provided
    if content is not None:
        if is_first:
            chunk["choices"][0]["delta"] = {"role": "assistant", "content": content}
        else:
            chunk["choices"][0]["delta"] = {"content": content}
    
    # Add meta only if provided
    if meta is not None:
        chunk["choices"][0]["delta"]["meta"] = meta
        
    return chunk

async def process_message_event(data: dict, is_first_chunk: bool, in_thinking_block: bool,
                                thinking_started: bool, thinking_content: list) -> Tuple[str, bool, bool, bool, list]:
    """Process message event with optimized string handling"""
    content = data.get("content", "")
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))
    result = ""

    # Check if it's the start of a thinking block
    if "```ys_think" in content and not thinking_started:
        thinking_started = True
        in_thinking_block = True
        # Send thinking block start marker
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="<Thinking>\n\n",
            is_first=is_first_chunk
        )
        result = f"data: \{json.dumps(chunk, ensure_ascii=False)\}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Check if it's the end of a thinking block
    if "```" in content and in_thinking_block:
        in_thinking_block = False
        # Send thinking block end marker
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="\n</Thinking>\n\n"
        )
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # If in thinking block, collect thinking content
    if in_thinking_block:
        thinking_content.append(content)
        # Also send content in thinking block, but mark as thinking content
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content=content
        )
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Clean content, remove thinking blocks
    content = clean_thinking_content(content)
    if not content:  # Skip if content is empty after cleaning
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Send normal content
    chunk = create_chunk(
        sse_id=sse_id,
        created=created,
        content=content,
        is_first=is_first_chunk
    )
    result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    return result, in_thinking_block, thinking_started, False, thinking_content

def process_generate_end_event(data: dict, in_thinking_block: bool, thinking_content: list) -> List[str]:
    """Process generation end event with optimized result creation"""
    result = []
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))

    # If thinking block hasn't ended, send end marker
    if in_thinking_block:
        end_thinking_chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="\n<Thinking>
</Thinking>\n\n"
        )
        result.append(f"data: {json.dumps(end_thinking_chunk, ensure_ascii=False)}\n\n")

    # Add metadata
    meta_chunk = create_chunk(
        sse_id=sse_id,
        created=created,
        meta={"thinking_content": "".join(thinking_content) if thinking_content else None}
    )
    result.append(f"data: {json.dumps(meta_chunk, ensure_ascii=False)}\n\n")

    # Send end marker
    end_chunk = create_chunk(
        sse_id=sse_id,
        created=created,
        finish_reason="stop"
    )
    result.append(f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n")
    result.append("data: [DONE]\n\n")
    return result

async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0) -> AsyncGenerator[str, None]:
    """Generate response with optimized streaming and error handling"""
    # Ensure session is initialized
    await session_manager.refresh_if_needed()

    timestamp = generate_timestamp()
    # Use the last message as the query
    query = messages[-1]['content']
    
    # Prepare conversation context from previous messages
    context = []
    for msg in messages[:-1]:
        role_prefix = "用户: " if msg["role"] == "user" else "助手: "
        context.append(f"{role_prefix}{msg['content']}")
    
    # Build payload with optimized structure
    payload = {
        'userId': session_manager.user_id,
        'botId': Config.BOT_ID,
        'botAlias': 'custom',
        'query': query,
        'isRetry': False,
        'breakingStrategy': 0,
        'isNewConversation': True,
        'mediaInfos': [],
        'turnIndex': 0,
        'rewriteQuery': '',
        'conversationId': session_manager.conversation_id,
        'capabilities': [
            {
                'capability': 'otherBot',
                'key': 'deep_think',
                'botId': 210029,
                'title': '深度思考(R1)',
                'defaultSelected': False,
            },
        ],
        'attachmentInfo': {'url': {'infoList': []}},
        'inputWay': 'proactive',
        'pureQuery': '',
    }
    
    # Add context if available
    if context:
        payload['context'] = "\n".join(context)
    
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)

    # Create streaming request headers
    headers = create_common_headers(timestamp, digest, session_manager.token, session_manager.device_id)
    headers.update({
        'accept': 'text/event-stream, text/event-stream',
        'x-yuanshi-appversionname': '3.1.0',
    })

    try:
        # Use the shared HTTP client for better connection reuse
        client = await session_manager.http_client
        
        # Process streaming response with optimized buffer handling
        async with client.stream('POST', f"{Config.BASE_URL}/core/conversation/chat/v1",
                                headers=headers, content=data) as response:
            response.raise_for_status()

            # Process streaming response
            is_first_chunk = True
            current_event = None
            in_thinking_block = False
            thinking_content = []
            thinking_started = False
            buffer = ""

            async for chunk in response.aiter_bytes(1024):
                buffer += chunk.decode('utf-8')
                lines = buffer.split('\n')
                
                # Process complete lines
                if len(lines) > 1:
                    buffer = lines[-1]  # Keep the last incomplete line in buffer
                    
                    for line in lines[:-1]:
                        line = line.strip()
                        if not line:
                            current_event = None
                            continue

                        # Parse event type
                        if line.startswith("event:"):
                            current_event = line[len("event:"):].strip()
                            continue

                        # Process data line
                        elif line.startswith("data:"):
                            json_str = line[len("data:"):].strip()
                            try:
                                data = json.loads(json_str)

                                # Process message event
                                if current_event == "message":
                                    result, in_thinking_block, thinking_started, is_first_chunk, thinking_content = await process_message_event(
                                        data, is_first_chunk, in_thinking_block, thinking_started, thinking_content
                                    )
                                    if result:
                                        yield result

                                # Process generation end event
                                elif current_event == "generateEnd":
                                    for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                        yield chunk

                            except json.JSONDecodeError as e:
                                logger.error(f"JSON parsing error: {e}")
                                continue

    except httpx.RequestError as e:
        logger.error(f"Error generating response: {e}")
        # Try to reinitialize session
        try:
            await session_manager.initialize()
            logger.info("Session reinitialized")
        except Exception as re_init_error:
            logger.error(f"Failed to reinitialize session: {re_init_error}")
        raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")

@app.get("/")
async def root():
    """Root endpoint with minimal processing"""
    return {"status": "ok", "message": "hefengfan API successfully deployed!"}

@app.get("/v1/models")
async def list_models():
    """List available models with cached response"""
    current_time = int(time.time())
    models_data = [
        ModelData(
            id=Config.DEFAULT_MODEL,
            created=current_time,
            owned_by="wenxiaobai",
            root=Config.DEFAULT_MODEL,
            permission=[{
                "id": f"modelperm-{Config.DEFAULT_MODEL}",
                "object": "model_permission",
                "created": current_time,
                "allow_create_engine": False,
                "allow_sampling": True,
                "allow_logprobs": True,
                "allow_search_indices": False,
                "allow_view": True,
                "allow_fine_tuning": False,
                "organization": "wenxiaobai",
                "group": None,
                "is_blocking": False
            }]
        )
    ]

    return {"object": "list", "data": models_data}

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, authorization: str = Header(None)):
    """Process chat completion requests with optimized memory usage"""
    # Verify API key
    await verify_api_key(authorization)

    # Log request with minimal info
    logger.info(f"Chat request: model={request.model}, stream={request.stream}")
    
    # Convert messages to dict format
    messages = [msg.model_dump() for msg in request.messages]

    if not request.stream:
        # Non-streaming response processing
        content = ""
        thinking_content = ""
        meta = None
        in_thinking = False

        async for chunk_str in generate_response(
                messages=messages,
                model=request.model,
                temperature=request.temperature,
                stream=True,  # Still use streaming internally
                max_tokens=request.max_tokens,
                presence_penalty=request.presence_penalty,
                frequency_penalty=request.frequency_penalty,
                top_p=request.top_p
        ):
            try:
                if chunk_str.startswith("data: ") and not chunk_str.startswith("data: [DONE]"):
                    chunk = json.loads(chunk_str[len("data: "):])
                    if "choices" in chunk and chunk["choices"]:
                        delta = chunk["choices"][0]["delta"]
                        if "content" in delta:
                            content_part = delta["content"]

                            # Handle thinking block markers
                            if content_part == "<Thinking>\n\n":
                                in_thinking = True
                                continue
                            elif content_part == "\n</Thinking>\n\n":
                                in_thinking = False
                                continue

                            # Collect content
                            if in_thinking:
                                thinking_content += content_part
                            else:
                                content += content_part

                        # Collect metadata
                        if "meta" in delta:
                            meta = delta["meta"]
            except Exception as e:
                logger.error(f"Error processing non-streaming response: {e}")

        # Build complete response
        return {
            "id": str(uuid.uuid4()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{
                "message": {
                    "role": "assistant",
                    "reasoning_content": f"<Thinking>\n\{thinking_content\}\n</Thinking>" if thinking_content else None,
                    "content": content,
                    "meta": meta
                },
                "finish_reason": "stop"
            }]
        }

    # Streaming response
    return StreamingResponse(
        generate_response(
            messages=messages,
            model=request.model,
            temperature=request.temperature,
            stream=request.stream,
            max_tokens=request.max_tokens,
            presence_penalty=request.presence_penalty,
            frequency_penalty=request.frequency_penalty,
            top_p=request.top_p
        ),
        media_type="text/event-stream"
    )

@app.get("/health")
async def health_check():
    """Health check endpoint with minimal processing"""
    if session_manager.is_initialized():
        return {"status": "ok", "session": "active"}
    else:
        return {"status": "degraded", "session": "inactive"}

# Add memory usage endpoint for monitoring
@app.get("/memory")
async def memory_usage():
    """Return current memory usage statistics"""
    import psutil
    process = psutil.Process()
    memory_info = process.memory_info()
    return {
        "rss": memory_info.rss / (1024 * 1024),  # RSS in MB
        "vms": memory_info.vms / (1024 * 1024),  # VMS in MB
        "connections": len(process.connections()),
        "threads": process.num_threads()
    }

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
