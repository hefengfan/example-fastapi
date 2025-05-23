import asyncio
import json
import random
import uuid
import datetime
import time
import re
import gc
import psutil
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

# Use lifespan for more efficient resource management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources on startup
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        http2=True
    )
    app.state.http_client = client
    app.state.session_lock = asyncio.Lock()
    app.state.active_requests = 0
    app.state.max_requests = 50  # Maximum concurrent requests
    app.state.last_activity = time.time()
    app.state.memory_stats = {
        "last_check": time.time(),
        "peak_memory": 0,
        "current_memory": 0,
        "gc_collections": 0
    }
    
    # Initialize session on startup
    try:
        await session_manager.refresh_if_needed(client)
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
    
    # Start background tasks
    app.state.background_tasks = []
    app.state.background_tasks.append(asyncio.create_task(memory_cleanup_task(app)))
    app.state.background_tasks.append(asyncio.create_task(refresh_session_task()))
    
    yield
    
    # Cancel background tasks
    for task in app.state.background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    # Clean up resources on shutdown
    await client.aclose()
    gc.collect()  # Force garbage collection

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
    REQUEST_TIMEOUT = 30.0  # seconds
    CONNECT_TIMEOUT = 5.0  # seconds
    MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "50"))
    EMPTY_RESPONSE_TIMEOUT = 10.0  # seconds to wait before considering a response empty
    MEMORY_CLEANUP_INTERVAL = 60  # seconds between memory cleanup checks
    IDLE_THRESHOLD = 300  # seconds of inactivity before aggressive cleanup
    MEMORY_THRESHOLD = 400 * 1024 * 1024  # 400MB threshold for aggressive cleanup
    CHUNK_SIZE = 4096  # Larger chunk size for smoother streaming
    STREAM_BUFFER_SIZE = 16384  # Buffer size for streaming responses

# Optimized session manager with minimal state
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None
        self.last_refresh = 0
        self.refresh_interval = 1800  # Refresh token every 30 minutes
        self.is_refreshing = False
        self.last_error_time = 0
        self.error_count = 0
        self.max_errors = 5
        self.error_reset_time = 300  # Reset error count after 5 minutes

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    async def refresh_if_needed(self, client=None):
        """Refresh session if needed with error tracking"""
        if self.is_refreshing:
            # Wait for another refresh to complete
            await asyncio.sleep(0.5)
            return
        
        try:
            self.is_refreshing = True
            current_time = time.time()
            
            # Check if we need to reset error count
            if current_time - self.last_error_time > self.error_reset_time:
                self.error_count = 0
            
            # Check if refresh is needed
            if (not self.is_initialized() or 
                current_time - self.last_refresh > self.refresh_interval or
                self.error_count >= self.max_errors):
                
                close_client = False
                if client is None:
                    client = httpx.AsyncClient(timeout=httpx.Timeout(Config.CONNECT_TIMEOUT))
                    close_client = True
                
                try:
                    await self.initialize(client)
                    self.last_refresh = current_time
                    self.error_count = 0
                    logger.info("Session refreshed successfully")
                finally:
                    if close_client:
                        await client.aclose()
        except Exception as e:
            self.last_error_time = time.time()
            self.error_count += 1
            logger.error(f"Session refresh error: {e}")
        finally:
            self.is_refreshing = False

    async def initialize(self, client=None):
        """Initialize session with minimal overhead"""
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(Config.CONNECT_TIMEOUT))
            close_client = True
            
        try:
            self.device_id = generate_device_id()
            self.token, self.user_id = await get_auth_token(self.device_id, client)
            self.conversation_id = await create_conversation(self.device_id, self.token, self.user_id, client)
            logger.info(f"Session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")
        finally:
            if close_client:
                await client.aclose()

    def track_error(self):
        """Track error for potential session refresh"""
        self.last_error_time = time.time()
        self.error_count += 1
        logger.warning(f"Error tracked, count: {self.error_count}")

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
def get_memory_usage():
    """Get current memory usage of the process"""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss

async def perform_memory_cleanup(app, force=False):
    """Perform memory cleanup based on current usage and idle state"""
    current_time = time.time()
    current_memory = get_memory_usage()
    
    # Update memory stats
    app.state.memory_stats["current_memory"] = current_memory
    app.state.memory_stats["peak_memory"] = max(app.state.memory_stats["peak_memory"], current_memory)
    app.state.memory_stats["last_check"] = current_time
    
    # Check if we should perform cleanup
    is_idle = current_time - app.state.last_activity > Config.IDLE_THRESHOLD
    memory_high = current_memory > Config.MEMORY_THRESHOLD
    
    if force or is_idle or memory_high:
        # Perform aggressive cleanup
        logger.info(f"Performing memory cleanup: idle={is_idle}, memory={current_memory/1024/1024:.2f}MB, force={force}")
        
        # Run garbage collection
        collected = await run_in_threadpool(gc.collect, 2)  # Full collection
        app.state.memory_stats["gc_collections"] += 1
        
        # Clear internal caches
        gc.collect()
        
        # If on Linux, try to release memory back to the system
        if hasattr(gc, "freeze"):
            gc.freeze()
        
        if os.name == 'posix':
            # Try to release memory back to the OS on Linux
            try:
                libc = ctypes.CDLL('libc.so.6')
                libc.malloc_trim(0)
            except:
                pass
            
            # Send SIGUSR1 to trigger memory release in Python runtime
            try:
                os.kill(os.getpid(), signal.SIGUSR1)
            except:
                pass
        
        # Log memory after cleanup
        new_memory = get_memory_usage()
        logger.info(f"Memory cleanup complete: before={current_memory/1024/1024:.2f}MB, after={new_memory/1024/1024:.2f}MB, freed={(current_memory-new_memory)/1024/1024:.2f}MB, collected={collected}")
        
        return True
    return False

async def memory_cleanup_task(app):
    """Background task to periodically clean up memory"""
    while True:
        try:
            await perform_memory_cleanup(app)
        except Exception as e:
            logger.error(f"Error in memory cleanup task: {e}")
        
        await asyncio.sleep(Config.MEMORY_CLEANUP_INTERVAL)

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
                content=data,
                timeout=Config.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            return result['data']['token'], result['data']['user']['id']
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            if attempt < Config.MAX_RETRIES - 1:
                await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
                continue
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
                content=data,
                timeout=Config.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response.json()['data']
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            if attempt < Config.MAX_RETRIES - 1:
                await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
                continue
            logger.error(f"Failed to create conversation after {Config.MAX_RETRIES} attempts: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse conversation response: {e}")
            raise HTTPException(status_code=500, detail="Server returned invalid conversation data")

# Optimized content processing functions
def is_thinking_content(content: str) -> bool:
    """Check if content is thinking process - fixed escape sequence"""
    return "\`\`\`ys_think" in content

def clean_thinking_content(content: str) -> str:
    """Clean thinking process content, remove special markers"""
    # Remove entire thinking block
    if "\`\`\`ys_think" in content:
        # Use regex to remove entire thinking block
        cleaned = re.sub(r'\`\`\`ys_think.*?\`\`\`', '', content, flags=re.DOTALL)
        # If only whitespace remains after cleaning, return empty string
        if cleaned and cleaned.strip():
            return cleaned.strip()
        return ""
    return content

# Function to remove reference patterns like [数字](@ref)
def remove_reference_patterns(content: str) -> str:
    """Remove reference patterns like [数字](@ref) from content"""
    return re.sub(r'\[\d+\]$$@ref$$', '', content)

# Optimized response chunk creation
def create_chunk(sse_id: str, created: int, content: Optional[str] = None,
                 is_first: bool = False, meta: Optional[dict] = None,
                 finish_reason: Optional[str] = None) -> dict:
    """Create response chunk with minimal overhead"""
    delta = {}

    if content is not None:
        # Clean content of reference patterns
        content = remove_reference_patterns(content)
        
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

    # Check if it's the start of a thinking block
    if "\`\`\`ys_think" in content and not thinking_started:
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
    if "\`\`\`" in content and in_thinking_block:
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

# Function to check if a response is empty
async def is_empty_response(response_stream, timeout=Config.EMPTY_RESPONSE_TIMEOUT):
    """Check if a response stream is empty within the timeout period"""
    try:
        # Set a timeout for the first chunk
        first_chunk_task = asyncio.create_task(response_stream.__anext__())
        await asyncio.wait_for(first_chunk_task, timeout=timeout)
        return False  # Got a chunk, not empty
    except (asyncio.TimeoutError, StopAsyncIteration):
        return True  # No chunks within timeout, consider empty

# Enhanced streaming response generator
async def enhanced_streaming_generator(response, request_id):
    """Enhanced streaming generator for smoother response delivery"""
    buffer = bytearray()
    is_first_chunk = True
    current_event = None
    in_thinking_block = False
    thinking_content = []
    thinking_started = False
    last_activity = time.time()
    has_yielded_content = False
    pending_lines = []
    
    # Process streaming response with optimized buffering
    async for raw_chunk in response.aiter_bytes(Config.CHUNK_SIZE):
        last_activity = time.time()
        has_yielded_content = True
        
        # Add to buffer
        buffer.extend(raw_chunk)
        
        # Process complete lines
        while b'\n' in buffer:
            pos = buffer.find(b'\n')
            line = buffer[:pos].decode('utf-8', errors='replace').strip()
            buffer = buffer[pos + 1:]
            
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
                            pending_lines.append(result)
                    
                    # Process generation end event
                    elif current_event == "generateEnd":
                        pending_lines.extend(process_generate_end_event(data, in_thinking_block, thinking_content))
                
                except json.JSONDecodeError as e:
                    logger.error(f"JSON parsing error: {e}")
                    continue
        
        # Yield pending lines in batches for smoother streaming
        if pending_lines:
            yield ''.join(pending_lines)
            pending_lines = []
    
    # Yield any remaining lines
    if pending_lines:
        yield ''.join(pending_lines)

# Optimized response generation
async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0, 
                            client=None, request_id=None, app=None) -> AsyncGenerator[str, None]:
    """Generate response with true streaming and minimal memory usage"""
    # Update last activity time
    if app:
        app.state.last_activity = time.time()
    
    # Ensure session is initialized
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(Config.REQUEST_TIMEOUT, connect=Config.CONNECT_TIMEOUT))
        close_client = True
    
    empty_response_count = 0
    max_empty_responses = 3
    
    for attempt in range(Config.MAX_RETRIES + 1):  # +1 to allow for session refresh
        try:
            await session_manager.refresh_if_needed(client)

            timestamp = generate_timestamp()
            payload = {
                'userId': session_manager.user_id,
                'botId': Config.BOT_ID,
                'botAlias': 'custom',
                'query': messages[-1]['content'],
                'isRetry': attempt > 0,  # Mark as retry if not first attempt
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

            # Use stream=True parameter for true streaming with optimized settings
            async with client.stream(
                'POST', 
                f"{Config.BASE_URL}/core/conversation/chat/v1",
                headers=headers, 
                content=data,
                timeout=httpx.Timeout(Config.REQUEST_TIMEOUT * 2)  # Longer timeout for streaming
            ) as response:
                response.raise_for_status()
                
                # Check for empty response
                if await is_empty_response(response.aiter_bytes(Config.CHUNK_SIZE)):
                    empty_response_count += 1
                    logger.warning(f"Empty response detected (attempt {attempt+1}, count {empty_response_count})")
                    
                    if empty_response_count >= max_empty_responses:
                        # Force session refresh after multiple empty responses
                        logger.warning("Multiple empty responses, forcing session refresh")
                        await session_manager.initialize(client)
                        empty_response_count = 0
                    
                    if attempt < Config.MAX_RETRIES:
                        await asyncio.sleep(Config.RETRY_DELAY * (attempt + 1))
                        continue
                    else:
                        # Return error message as a response
                        error_id = str(uuid.uuid4())
                        created = int(time.time())
                        error_chunk = create_chunk(
                            sse_id=error_id,
                            created=created,
                            content="I'm sorry, but I couldn't generate a response at this time. Please try again.",
                            is_first=True
                        )
                        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                        
                        end_chunk = create_chunk(
                            sse_id=error_id,
                            created=created,
                            finish_reason="stop"
                        )
                        yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                
                # Use enhanced streaming generator for smoother output
                async for chunk in enhanced_streaming_generator(response, request_id):
                    if app:
                        app.state.last_activity = time.time()
                    yield chunk
                
                # Successfully completed
                return
                
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f"Generate response error (attempt {attempt+1}): {e}")
            session_manager.track_error()
            
            if attempt < Config.MAX_RETRIES:
                # Exponential backoff
                await asyncio.sleep(Config.RETRY_DELAY * (2 ** attempt))
                
                # Force session refresh on certain errors
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403, 429):
                    logger.warning("Auth error detected, forcing session refresh")
                    await session_manager.initialize(client)
                
                continue
            else:
                # Return error message after all retries
                error_id = str(uuid.uuid4())
                created = int(time.time())
                error_chunk = create_chunk(
                    sse_id=error_id,
                    created=created,
                    content="I'm sorry, but I encountered an error while generating a response. Please try again later.",
                    is_first=True
                )
                yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                
                end_chunk = create_chunk(
                    sse_id=error_id,
                    created=created,
                    finish_reason="stop"
                )
                yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
        finally:
            if close_client:
                await client.aclose()

# Background task to refresh session periodically
async def refresh_session_task():
    """Background task to refresh session periodically"""
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(Config.CONNECT_TIMEOUT)) as client:
                await session_manager.refresh_if_needed(client)
        except Exception as e:
            logger.error(f"Background session refresh error: {e}")
        
        # Sleep for half the refresh interval
        await asyncio.sleep(session_manager.refresh_interval / 2)

# API endpoints
@app.get("/")
async def health():
    return {"status": "ok", "message": "hefengfan API successfully deployed!"}

@app.get("/v1/models")
async def list_models(req: Request):
    """List available models"""
    # Update last activity time
    req.app.state.last_activity = time.time()
    
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
async def chat_completions(
    request: ChatCompletionRequest, 
    authorization: str = Header(None), 
    req: Request = None,
    background_tasks: BackgroundTasks = None
):
    """Process chat completion requests with memory optimization and request limiting"""
    # Verify API key
    await verify_api_key(authorization)
    
    # Update last activity time
    req.app.state.last_activity = time.time()
    
    # Generate unique request ID for tracking
    request_id = str(uuid.uuid4())
    
    # Check if we're at max capacity
    if hasattr(req.app.state, 'active_requests') and req.app.state.active_requests >= Config.MAX_CONCURRENT_REQUESTS:
        logger.warning(f"Request {request_id} rejected: server at capacity ({req.app.state.active_requests}/{Config.MAX_CONCURRENT_REQUESTS})")
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    
    # Increment active requests counter
    if hasattr(req.app.state, 'active_requests'):
        req.app.state.active_requests += 1
        logger.info(f"Request {request_id} started. Active requests: {req.app.state.active_requests}")
    
    try:
        # Add request log
        logger.info(f"Received chat request {request_id}: model={request.model}, stream={request.stream}")
        messages = [msg.model_dump() for msg in request.messages]

        # Get client from app state
        client = req.app.state.http_client if hasattr(req.app.state, 'http_client') else None

        if not request.stream:
            # Non-streaming response processing
            content = ""
            thinking_content = ""
            meta = None
            in_thinking = False
            
            # Schedule background session refresh for long-running requests
            if background_tasks:
                background_tasks.add_task(session_manager.refresh_if_needed, client)

            async for chunk_str in generate_response(
                    messages=messages,
                    model=request.model,
                    temperature=request.temperature,
                    stream=True,  # Still use streaming internally
                    max_tokens=request.max_tokens,
                    presence_penalty=request.presence_penalty,
                    frequency_penalty=request.frequency_penalty,
                    top_p=request.top_p,
                    client=client,
                    request_id=request_id,
                    app=req.app
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

            # Clean content of reference patterns
            content = remove_reference_patterns(content)
            
            # Build complete response
            return {
                "id": f"chatcmpl-{request_id}",
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

        # Streaming response with enhanced generator
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
                client=client,
                request_id=request_id,
                app=req.app
            ),
            media_type="text/event-stream"
        )
    finally:
        # Decrement active requests counter when done
        if hasattr(req.app.state, 'active_requests'):
            req.app.state.active_requests -= 1
            logger.info(f"Request {request_id} completed. Active requests: {req.app.state.active_requests}")

@app.get("/health")
async def health_check(req: Request):
    """Health check endpoint with detailed status"""
    # Update last activity time
    req.app.state.last_activity = time.time()
    
    # Get current memory usage
    current_memory = get_memory_usage()
    memory_mb = current_memory / (1024 * 1024)
    
    status = {
        "status": "ok" if session_manager.is_initialized() else "degraded",
        "session": {
            "initialized": session_manager.is_initialized(),
            "last_refresh": datetime.datetime.fromtimestamp(session_manager.last_refresh).isoformat() if session_manager.last_refresh else None,
            "error_count": session_manager.error_count
        },
        "memory": {
            "current": f"{memory_mb:.2f}MB",
            "peak": f"{req.app.state.memory_stats['peak_memory'] / (1024 * 1024):.2f}MB" if hasattr(req.app.state, 'memory_stats') else "unknown",
            "active_requests": req.app.state.active_requests if hasattr(req.app.state, 'active_requests') else "unknown",
            "gc_collections": req.app.state.memory_stats.get("gc_collections", 0) if hasattr(req.app.state, 'memory_stats') else 0
        },
        "uptime": {
            "last_activity": datetime.datetime.fromtimestamp(req.app.state.last_activity).isoformat() if hasattr(req.app.state, 'last_activity') else None,
            "idle": time.time() - req.app.state.last_activity > Config.IDLE_THRESHOLD if hasattr(req.app.state, 'last_activity') else False
        },
        "timestamp": datetime.datetime.now().isoformat()
    }
    
    # Return degraded status if error count is high or memory usage is high
    if session_manager.error_count >= session_manager.max_errors // 2 or memory_mb > Config.MEMORY_THRESHOLD / (1024 * 1024):
        status["status"] = "degraded"
    
    # Trigger memory cleanup if needed
    if memory_mb > Config.MEMORY_THRESHOLD / (1024 * 1024) * 0.8:
        background_tasks = BackgroundTasks()
        background_tasks.add_task(perform_memory_cleanup, req.app, True)
        
    return status

@app.post("/cleanup")
async def force_cleanup(req: Request):
    """Force memory cleanup"""
    # Update last activity time
    req.app.state.last_activity = time.time()
    
    # Get memory before cleanup
    before_memory = get_memory_usage()
    
    # Perform cleanup
    cleaned = await perform_memory_cleanup(req.app, force=True)
    
    # Get memory after cleanup
    after_memory = get_memory_usage()
    
    return {
        "status": "success",
        "cleaned": cleaned,
        "memory_before": f"{before_memory / (1024 * 1024):.2f}MB",
        "memory_after": f"{after_memory / (1024 * 1024):.2f}MB",
        "freed": f"{(before_memory - after_memory) / (1024 * 1024):.2f}MB",
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.post("/refresh")
async def force_refresh(req: Request):
    """Force session refresh"""
    # Update last activity time
    req.app.state.last_activity = time.time()
    
    # Get client from app state
    client = req.app.state.http_client if hasattr(req.app.state, 'http_client') else None
    
    # Force session refresh
    try:
        await session_manager.initialize(client)
        return {
            "status": "success",
            "message": "Session refreshed successfully",
            "session": {
                "user_id": session_manager.user_id,
                "conversation_id": session_manager.conversation_id,
                "last_refresh": datetime.datetime.now().isoformat()
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to refresh session: {str(e)}"
        }

# Import ctypes for memory management on Linux
try:
    import ctypes
except ImportError:
    ctypes = None

