import asyncio
import json
import random
import uuid
import datetime
import time
import re
import gc
import os
import signal
from fastapi import FastAPI, HTTPException, Header, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple
import httpx
import logging
import hashlib
import base64
import hmac
from starlette.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi.concurrency import run_in_threadpool

# Configure logging with minimal overhead
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Memory management settings
MAX_MEMORY_PERCENT = 80  # Trigger cleanup when memory usage exceeds this percentage
MEMORY_CHECK_INTERVAL = 300  # Check memory every 5 minutes
last_memory_check = 0
last_gc_run = 0

# Request tracking for health monitoring
active_requests = 0
total_requests = 0
failed_requests = 0
empty_responses = 0
last_error_time = 0

# Use lifespan for more efficient resource management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources on startup
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(150), 
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        http2=True  # Enable HTTP/2 for better connection reuse
    )
    app.state.http_client = client
    
    # Initialize session on startup
    try:
        await session_manager.refresh_if_needed(client)
        logger.info("Initial session established successfully")
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
    
    # Set up signal handlers for graceful shutdown
    def handle_sigterm(signum, frame):
        logger.info("Received SIGTERM, shutting down gracefully")
    
    signal.signal(signal.SIGTERM, handle_sigterm)
    
    yield
    
    # Clean up resources on shutdown
    await client.aclose()
    logger.info("HTTP client closed")
    await run_in_threadpool(gc.collect)
    logger.info("Final garbage collection completed")

# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Add CORS middleware with minimal overhead
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration class with minimal memory footprint
class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # seconds
    CONTEXT_WINDOW_SIZE = 8192  # Maximum context size

# Optimized session manager with minimal state
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None
        self.last_refresh = 0
        self.refresh_interval = 1800  # Refresh token every 30 minutes
        self.error_count = 0
        self.max_errors = 5
        self.last_error_time = 0
        self.error_cooldown = 60  # Wait 60 seconds after multiple errors

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    async def refresh_if_needed(self, client=None):
        """Refresh session if needed"""
        current_time = time.time()
        
        # Check if we're in error cooldown
        if self.error_count >= self.max_errors and current_time - self.last_error_time < self.error_cooldown:
            logger.warning("In error cooldown, delaying refresh")
            return
        
        # Check if refresh is needed
        if (not self.is_initialized() or 
            current_time - self.last_refresh > self.refresh_interval):
            await self.initialize(client)
            self.last_refresh = current_time
            self.error_count = 0  # Reset error count after successful refresh

    async def initialize(self, client=None):
        """Initialize session with minimal overhead"""
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(30))
            close_client = True
            
        try:
            self.device_id = generate_device_id()
            self.token, self.user_id = await get_auth_token(self.device_id, client)
            self.conversation_id = await create_conversation(self.device_id, self.token, self.user_id, client)
            logger.info(f"Session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")
        except Exception as e:
            self.error_count += 1
            self.last_error_time = time.time()
            logger.error(f"Session initialization error: {e}, error count: {self.error_count}")
            raise
        finally:
            if close_client:
                await client.aclose()

# Create session manager instance
session_manager = SessionManager()

# Optimized Pydantic models with minimal validation overhead
class Message(BaseModel):
    role: str
    content: str
    name: Optional[str] = None

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0
    frequency_penalty: Optional[float] = 0
    user: Optional[str] = None

class ModelData(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str
    permission: List[Dict[str, Any]] = []
    root: str
    parent: Optional[str] = None

# Memory management functions
async def check_memory_usage(background_tasks: BackgroundTasks):
    """Check memory usage and trigger cleanup if needed"""
    global last_memory_check, last_gc_run
    
    current_time = time.time()
    if current_time - last_memory_check < MEMORY_CHECK_INTERVAL:
        return
    
    last_memory_check = current_time
    
    try:
        # Get memory info - this works on Linux systems
        with open('/proc/self/status') as f:
            for line in f:
                if 'VmRSS:' in line:
                    mem_usage = int(line.split()[1])
                    break
        
        # Get total system memory
        with open('/proc/meminfo') as f:
            for line in f:
                if 'MemTotal:' in line:
                    total_mem = int(line.split()[1])
                    break
        
        mem_percent = (mem_usage / total_mem) * 100
        logger.info(f"Current memory usage: {mem_usage}KB / {total_mem}KB ({mem_percent:.1f}%)")
        
        if mem_percent > MAX_MEMORY_PERCENT and current_time - last_gc_run > 60:
            logger.warning(f"Memory usage high ({mem_percent:.1f}%), triggering cleanup")
            background_tasks.add_task(cleanup_memory)
    except Exception as e:
        logger.error(f"Error checking memory: {e}")

async def cleanup_memory():
    """Clean up memory by forcing garbage collection"""
    global last_gc_run
    
    logger.info("Running memory cleanup")
    last_gc_run = time.time()
    
    # Run garbage collection
    gc.collect()
    
    # Try to release memory back to the OS (Linux-specific)
    try:
        import ctypes
        libc = ctypes.CDLL('libc.so.6')
        libc.malloc_trim(0)
        logger.info("Released memory back to OS")
    except Exception as e:
        logger.error(f"Error releasing memory to OS: {e}")

# Utility functions optimized for memory efficiency
def generate_device_id() -> str:
    """Generate device ID with minimal overhead"""
    return f"{uuid.uuid4().hex}_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"

def generate_timestamp() -> str:
    """Generate UTC timestamp string with minimal overhead"""
    timestamp_ms = int(time.time() * 1000) + 559
    utc_time = datetime.datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    return utc_time.strftime('%a, %d %b %Y %H:%M:%S GMT')

def calculate_sha256(data: str) -> str:
    """Calculate SHA-256 digest with minimal overhead"""
    sha256 = hashlib.sha256(data.encode()).digest()
    return base64.b64encode(sha256).decode()

def generate_signature(timestamp: str, digest: str) -> str:
    """Generate request signature with minimal overhead"""
    message = f"x-date: {timestamp}\ndigest: SHA-256={digest}"
    signature = hmac.new(
        Config.API_KEY.encode(),
        message.encode(),
        hashlib.sha1
    ).digest()
    return base64.b64encode(signature).decode()

def create_common_headers(timestamp: str, digest: str, token: Optional[str] = None,
                          device_id: Optional[str] = None) -> dict:
    """Create common request headers with minimal overhead"""
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'zh-CN,zh;q=0.9',
        'authorization': f'hmac username="web.1.0.beta", algorithm="hmac-sha1", headers="x-date digest", signature="{generate_signature(timestamp, digest)}"',
        'content-type': 'application/json',
        'digest': f'SHA-256={digest}',
        'origin': 'https://www.wenxiaobai.com',
        'referer': 'https://www.wenxiaobai.com/',
        'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0',
        'x-date': timestamp,
        'x-yuanshi-appname': 'wenxiaobai',
        'x-yuanshi-appversioncode': '2.1.5',
        'x-yuanshi-appversionname': '2.8.0',
        'x-yuanshi-channel': 'browser',
        'x-yuanshi-devicemode': 'Edge',
        'x-yuanshi-deviceos': '134',
        'x-yuanshi-locale': 'zh',
        'x-yuanshi-platform': 'web',
        'x-yuanshi-timezone': 'Asia/Shanghai',
    }

    if token:
        headers['x-yuanshi-authorization'] = f'Bearer {token}'

    if device_id:
        headers['x-yuanshi-deviceid'] = device_id

    return headers

async def get_auth_token(device_id: str, client) -> Tuple[str, str]:
    """Get authentication token with minimal overhead"""
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

    for attempt in range(Config.MAX_RETRIES):
        try:
            response = await client.post(
                f"{Config.BASE_URL}/user/sessions",
                headers=headers,
                content=data
            )
            response.raise_for_status()
            result = response.json()
            return result['data']['token'], result['data']['user']['id']
        except httpx.RequestError as e:
            if attempt < Config.MAX_RETRIES - 1:
                logger.warning(f"Auth token request failed (attempt {attempt+1}/{Config.MAX_RETRIES}): {e}")
                await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
            else:
                logger.error(f"Failed to get auth token after {Config.MAX_RETRIES} attempts: {e}")
                raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse auth response: {e}")
            raise HTTPException(status_code=500, detail="Server returned invalid authentication data")

async def create_conversation(device_id: str, token: str, user_id: str, client) -> str:
    """Create new conversation with minimal overhead"""
    timestamp = generate_timestamp()
    payload = {'visitorId': device_id}
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)

    headers = create_common_headers(timestamp, digest, token, device_id)

    for attempt in range(Config.MAX_RETRIES):
        try:
            response = await client.post(
                f"{Config.BASE_URL}/core/conversations/users/{user_id}/bots/{Config.BOT_ID}/conversation",
                headers=headers,
                content=data
            )
            response.raise_for_status()
            return response.json()['data']
        except httpx.RequestError as e:
            if attempt < Config.MAX_RETRIES - 1:
                logger.warning(f"Create conversation request failed (attempt {attempt+1}/{Config.MAX_RETRIES}): {e}")
                await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
            else:
                logger.error(f"Failed to create conversation after {Config.MAX_RETRIES} attempts: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse conversation response: {e}")
            raise HTTPException(status_code=500, detail="Server returned invalid conversation data")

# Optimized content processing functions
def is_thinking_content(content: str) -> bool:
    """Check if content is thinking process"""
    return "```ys_think" in content

def clean_thinking_content(content: str) -> str:
    """Clean thinking process content, remove special markers"""
    # Remove entire thinking block
    if "```ys_think" in content:
        # Use regex to remove entire thinking block
        cleaned = re.sub(r'```ys_think.*?```', '', content, flags=re.DOTALL)
        # If only whitespace remains after cleaning, return empty string
        if cleaned and cleaned.strip():
            return cleaned.strip()
        return ""
    return content

def clean_reference_markers(content: str) -> str:
    """Remove reference markers like [这是一个数字](@ref) from content"""
    # Remove reference markers
    cleaned = re.sub(r'\[\s*[^\]]+\s*\]\s*$$@ref$$', '', content)
    return cleaned

# Optimized response chunk creation
def create_chunk(sse_id: str, created: int, content: Optional[str] = None,
                 is_first: bool = False, meta: Optional[dict] = None,
                 finish_reason: Optional[str] = None) -> dict:
    """Create response chunk with minimal overhead"""
    delta = {}

    if content is not None:
        if is_first:
            delta = {"role": "assistant", "content": content}
        else:
            delta = {"content": content}

    if meta is not None:
        delta["meta"] = meta

    return {
        "id": f"chatcmpl-{sse_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": Config.DEFAULT_MODEL,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason
        }]
    }

async def process_message_event(data: dict, is_first_chunk: bool, in_thinking_block: bool,
                                thinking_started: bool, thinking_content: list) -> Tuple[str, bool, bool, bool, list]:
    """Process message event with minimal overhead"""
    content = data.get("content", "")
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))
    result = ""

    # Clean reference markers from content
    content = clean_reference_markers(content)

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
    """Process generation end event with minimal overhead"""
    result = []
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))

    # If thinking block hasn't ended yet, send end marker
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

# Optimized API key verification
async def verify_api_key(authorization: str = Header(None)):
    """Verify API key with minimal overhead"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing API key")

    api_key = authorization.replace("Bearer ", "").strip()
    if api_key != Config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key

# Prepare messages for better context understanding
def prepare_messages_for_context(messages: List[dict]) -> str:
    """Format messages to improve context understanding"""
    # Extract the last user message
    last_message = messages[-1]['content']
    
    # If there are previous messages, include them for context
    if len(messages) > 1:
        context = []
        for i, msg in enumerate(messages[:-1]):
            role_prefix = "User: " if msg['role'] == 'user' else "Assistant: "
            context.append(f"{role_prefix}{msg['content']}")
        
        # Format the context and the current message
        context_str = "\n\n".join(context)
        return f"Previous conversation:\n{context_str}\n\nCurrent question: {last_message}"
    
    return last_message

# Optimized response generation
async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0, 
                            client=None) -> AsyncGenerator[str, None]:
    """Generate response with true streaming and minimal memory usage"""
    global active_requests, total_requests, failed_requests, empty_responses, last_error_time
    
    active_requests += 1
    total_requests += 1
    empty_content = True
    response_started = False
    
    # Ensure session is initialized
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(150))
        close_client = True
    
    try:
        await session_manager.refresh_if_needed(client)

        # Format messages for better context understanding
        query = prepare_messages_for_context(messages)

        timestamp = generate_timestamp()
        payload = {
            'userId': session_manager.user_id,
            'botId': Config.BOT_ID,
            'botAlias': 'custom',
            'query': query,  # Use the formatted query with context
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
                    'capabilityRang': 0,
                    'defaultQuery': '',
                    'icon': 'https://wy-static.wenxiaobai.com/bot-capability/prod/%E6%B7%B1%E5%BA%A6%E6%80%9D%E8%80%83.png',
                    'minAppVersion': '',
                    'title': '深度思考(R1)',
                    'botId': 210029,
                    'botDesc': '深度回答这个问题（DeepSeek R1）',
                    'selectedIcon': 'https://wy-static.wenxiaobai.com/bot-capability/prod/%E6%B7%B1%E5%BA%A6%E6%80%9D%E8%80%83%E9%80%89%E4%B8%AD.png',
                    'botIcon': 'https://platform-dev-1319140468.cos.ap-nanjing.myqcloud.com/bot/avatar/2025/02/06/612cbff8-51e6-4c6a-8530-cb551bcfda56.webp',
                    'defaultHidden': False,
                    'defaultSelected': False,
                    'key': 'deep_think',
                    'promptMenu': False,
                    'isPromptMenu': False,
                    'defaultPlaceholder': '',
                    '_id': 'deep_think',
                },
            ],
            'attachmentInfo': {
                'url': {
                    'infoList': [],
                },
            },
            'inputWay': 'proactive',
            'pureQuery': '',
        }
        data = json.dumps(payload, separators=(',', ':'))
        digest = calculate_sha256(data)

        # Create special headers for streaming request
        headers = create_common_headers(timestamp, digest, session_manager.token, session_manager.device_id)
        headers.update({
            'accept': 'text/event-stream, text/event-stream',
            'x-yuanshi-appversioncode': '',
            'x-yuanshi-appversionname': '3.1.0',
        })

        # Use stream=True parameter for true streaming
        for attempt in range(Config.MAX_RETRIES):
            try:
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
                    last_activity = time.time()
                    timeout_seconds = 30  # Timeout for inactivity

                    async for raw_line in response.aiter_bytes(1024):
                        last_activity = time.time()
                        buffer += raw_line.decode('utf-8', errors='replace')
                        lines = buffer.split('\n')
                        buffer = lines.pop()  # Keep the last incomplete line in the buffer
                        
                        for line in lines:
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
                                            response_started = True
                                            empty_content = False
                                            yield result

                                    # Process generation end event
                                    elif current_event == "generateEnd":
                                        for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                            yield chunk
                                            response_started = True

                                except json.JSONDecodeError as e:
                                    logger.error(f"JSON parsing error: {e}")
                                    continue
                        
                        # Check for timeout
                        if time.time() - last_activity > timeout_seconds:
                            logger.warning("Response stream timed out due to inactivity")
                            break
                
                # If we got here without an exception, break the retry loop
                break
                
            except httpx.RequestError as e:
                failed_requests += 1
                last_error_time = time.time()
                
                if attempt < Config.MAX_RETRIES - 1:
                    logger.warning(f"Generate response request failed (attempt {attempt+1}/{Config.MAX_RETRIES}): {e}")
                    await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
                    # Try to reinitialize session before retrying
                    try:
                        await session_manager.initialize(client)
                        logger.info("Session reinitialized before retry")
                    except Exception as re_init_error:
                        logger.error(f"Failed to reinitialize session before retry: {re_init_error}")
                else:
                    logger.error(f"Failed to generate response after {Config.MAX_RETRIES} attempts: {e}")
                    # If we haven't started sending a response yet, we can raise an exception
                    if not response_started:
                        raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")
                    else:
                        # Otherwise, send an error message in the stream
                        error_chunk = create_chunk(
                            sse_id=str(uuid.uuid4()),
                            created=int(time.time()),
                            content="\n\nI apologize, but I encountered an error while generating the response. Please try again.",
                            is_first=is_first_chunk
                        )
                        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                        
                        # Send end marker
                        end_chunk = create_chunk(
                            sse_id=str(uuid.uuid4()),
                            created=int(time.time()),
                            finish_reason="stop"
                        )
                        yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
        
        # Check if we got an empty response
        if empty_content:
            empty_responses += 1
            logger.warning("Empty response detected")
            
            # Send a fallback message
            fallback_chunk = create_chunk(
                sse_id=str(uuid.uuid4()),
                created=int(time.time()),
                content="I apologize, but I couldn't generate a response. Please try rephrasing your question.",
                is_first=True
            )
            yield f"data: {json.dumps(fallback_chunk, ensure_ascii=False)}\n\n"
            
            # Send end marker
            end_chunk = create_chunk(
                sse_id=str(uuid.uuid4()),
                created=int(time.time()),
                finish_reason="stop"
            )
            yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            
    except Exception as e:
        failed_requests += 1
        last_error_time = time.time()
        logger.error(f"Unexpected error in generate_response: {e}")
        
        # If we haven't started sending a response yet, we can raise an exception
        if not response_started:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
        else:
            # Otherwise, send an error message in the stream
            error_chunk = create_chunk(
                sse_id=str(uuid.uuid4()),
                created=int(time.time()),
                content="\n\nI apologize, but I encountered an unexpected error. Please try again.",
                is_first=is_first_chunk
            )
            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
            
            # Send end marker
            end_chunk = create_chunk(
                sse_id=str(uuid.uuid4()),
                created=int(time.time()),
                finish_reason="stop"
            )
            yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
    finally:
        active_requests -= 1
        if close_client:
            await client.aclose()

# API endpoints
@app.get("/")
async def health():
    return {"status": "ok", "message": "hefengfan API successfully deployed!"}

@app.get("/v1/models")
async def list_models(background_tasks: BackgroundTasks):
    """List available models"""
    # Check memory usage in the background
    background_tasks.add_task(check_memory_usage, background_tasks)
    
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
async def chat_completions(request: ChatCompletionRequest, authorization: str = Header(None), 
                          req: Request = None, background_tasks: BackgroundTasks = None):
    """Process chat completion requests with memory optimization"""
    # Verify API key
    await verify_api_key(authorization)

    # Check memory usage in the background
    if background_tasks:
        background_tasks.add_task(check_memory_usage, background_tasks)

    # Add request log
    logger.info(f"Received chat request: model={request.model}, stream={request.stream}, messages={len(request.messages)}")
    messages = [msg.model_dump() for msg in request.messages]

    # Get client from app state
    client = req.app.state.http_client if hasattr(req.app.state, 'http_client') else None

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
                top_p=request.top_p,
                client=client
        ):
            try:
                if chunk_str.startswith("data: ") and not chunk_str.startswith("data: [DONE]"):
                    chunk = json.loads(chunk_str[len("data: "):])
                    if "choices" in chunk and chunk["choices"]:
                        delta = chunk["choices"][0]["delta"]
                        if "content" in delta:
                            content_part = delta["content"]

                            # Process thinking block markers
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

        # Clean reference markers from final content
        content = clean_reference_markers(content)

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
            top_p=request.top_p,
            client=client
        ),
        media_type="text/event-stream"
    )

@app.get("/health")
async def health_check(background_tasks: BackgroundTasks):
    """Health check endpoint with detailed status"""
    # Check memory usage in the background
    background_tasks.add_task(check_memory_usage, background_tasks)
    
    status = "ok"
    details = {
        "session": "active" if session_manager.is_initialized() else "inactive",
        "active_requests": active_requests,
        "total_requests": total_requests,
        "failed_requests": failed_requests,
        "empty_responses": empty_responses,
        "uptime": int(time.time() - app.state.startup_time) if hasattr(app.state, 'startup_time') else 0
    }
    
    # Check if we need to force garbage collection
    if active_requests == 0 and time.time() - last_gc_run > 300:  # 5 minutes since last GC
        background_tasks.add_task(cleanup_memory)
        details["gc_triggered"] = True
    
    # Determine status based on metrics
    if not session_manager.is_initialized():
        status = "degraded"
    elif failed_requests > 10 and failed_requests / max(1, total_requests) > 0.2:  # More than 20% failure rate
        status = "warning"
    elif empty_responses > 5 and empty_responses / max(1, total_requests) > 0.1:  # More than 10% empty responses
        status = "warning"
    
    return {"status": status, **details}

@app.on_event("startup")
async def startup_event():
    """Application startup event handler"""
    app.state.startup_time = time.time()
    logger.info("Application starting up")
    
    # Initialize session
    try:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30))
        await session_manager.initialize(client)
        await client.aclose()
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
