import asyncio
import json
import random
import uuid
import datetime
import time
import re
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple, Union
import httpx
import logging
import hashlib
import base64
import hmac
import gc
from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.concurrency import run_in_threadpool

# Configure logging with a more efficient format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Memory optimization: Use a custom context manager for app lifecycle
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources at startup
    logger.info("Application starting up")
    try:
        # Initialize session on startup
        await session_manager.initialize_async()
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
    
    yield
    
    # Clean up resources at shutdown
    logger.info("Application shutting down")
    # Force garbage collection to free memory
    gc.collect()


# Create FastAPI app with lifespan manager
app = FastAPI(lifespan=lifespan)

# Add CORS middleware
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Memory optimization: Add middleware to clean up memory after each request
class MemoryOptimizationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Run garbage collection in a separate thread to avoid blocking
        await run_in_threadpool(gc.collect)
        return response

app.add_middleware(MemoryOptimizationMiddleware)

# Configuration management
class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"
    # Memory optimization: Add connection pool limits
    MAX_CONNECTIONS = 10
    MAX_KEEPALIVE_CONNECTIONS = 5
    KEEPALIVE_EXPIRY = 30.0  # seconds
    # Add retry configuration
    MAX_RETRIES = 3
    RETRY_BACKOFF_FACTOR = 0.5


# Session management with improved error handling and async support
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None
        self._lock = asyncio.Lock()
        self._last_activity = time.time()
        self._client = None
        self._initialized = False
        self._error_count = 0
        self._max_errors = 5

    async def get_client(self):
        """Get or create an HTTP client with connection pooling"""
        if self._client is None:
            limits = httpx.Limits(
                max_connections=Config.MAX_CONNECTIONS,
                max_keepalive_connections=Config.MAX_KEEPALIVE_CONNECTIONS,
                keepalive_expiry=Config.KEEPALIVE_EXPIRY
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(150.0),
                limits=limits
            )
        return self._client

    async def initialize_async(self):
        """Initialize session asynchronously"""
        async with self._lock:
            try:
                self.device_id = await run_in_threadpool(generate_device_id)
                self.token, self.user_id = await self._get_auth_token_async(self.device_id)
                self.conversation_id = await self._create_conversation_async(self.device_id, self.token, self.user_id)
                self._last_activity = time.time()
                self._initialized = True
                self._error_count = 0
                logger.info(f"Session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")
            except Exception as e:
                self._error_count += 1
                logger.error(f"Session initialization failed: {e}")
                if self._error_count >= self._max_errors:
                    logger.critical("Too many initialization errors, service may be unavailable")
                raise

    def initialize(self):
        """Synchronous wrapper for initialize_async"""
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.initialize_async())

    def is_initialized(self):
        """Check if session is initialized"""
        return self._initialized and all([self.device_id, self.token, self.user_id, self.conversation_id])

    def is_expired(self, max_idle_time=3600):
        """Check if session is expired based on idle time"""
        return time.time() - self._last_activity > max_idle_time

    async def refresh_if_needed(self):
        """Refresh session if needed"""
        if not self.is_initialized() or self.is_expired():
            await self.initialize_async()
        else:
            self._last_activity = time.time()

    async def _get_auth_token_async(self, device_id: str) -> Tuple[str, str]:
        """Get authentication token asynchronously"""
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
        
        client = await self.get_client()
        try:
            response = await client.post(
                f"{Config.BASE_URL}/user/sessions",
                headers=headers,
                content=data,
                timeout=300
            )
            response.raise_for_status()
            result = response.json()
            return result['data']['token'], result['data']['user']['id']
        except httpx.RequestError as e:
            logger.error(f"Auth token request failed: {e}")
            raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Auth response parsing failed: {e}")
            raise HTTPException(status_code=500, detail="Server returned invalid authentication data")

    async def _create_conversation_async(self, device_id: str, token: str, user_id: str) -> str:
        """Create new conversation asynchronously"""
        timestamp = generate_timestamp()
        payload = {'visitorId': device_id}
        data = json.dumps(payload, separators=(',', ':'))
        digest = calculate_sha256(data)
        headers = create_common_headers(timestamp, digest, token, device_id)
        
        client = await self.get_client()
        try:
            response = await client.post(
                f"{Config.BASE_URL}/core/conversations/users/{user_id}/bots/{Config.BOT_ID}/conversation",
                headers=headers,
                content=data,
                timeout=300
            )
            response.raise_for_status()
            return response.json()['data']
        except httpx.RequestError as e:
            logger.error(f"Create conversation failed: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Conversation response parsing failed: {e}")
            raise HTTPException(status_code=500, detail="Server returned invalid conversation data")

    async def close(self):
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None


# Create session manager instance
session_manager = SessionManager()


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


def generate_device_id() -> str:
    """Generate device ID"""
    return f"{uuid.uuid4().hex}_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"


def generate_timestamp() -> str:
    """Generate UTC timestamp string"""
    timestamp_ms = int(time.time() * 1000) + 559
    utc_time = datetime.datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    return utc_time.strftime('%a, %d %b %Y %H:%M:%S GMT')


def calculate_sha256(data: str) -> str:
    """Calculate SHA-256 digest"""
    sha256 = hashlib.sha256(data.encode()).digest()
    return base64.b64encode(sha256).decode()


def generate_signature(timestamp: str, digest: str) -> str:
    """Generate request signature"""
    message = f"x-date: {timestamp}\ndigest: SHA-256={digest}"
    signature = hmac.new(
        Config.API_KEY.encode(),
        message.encode(),
        hashlib.sha1
    ).digest()
    return base64.b64encode(signature).decode()


def create_common_headers(timestamp: str, digest: str, token: Optional[str] = None,
                          device_id: Optional[str] = None) -> dict:
    """Create common request headers"""
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'zh-CN,zh;q=0.9',
        'authorization': f'hmac username="web.1.0.beta", algorithm="hmac-sha1", headers="x-date digest", signature="{generate_signature(timestamp, digest)}"',
        'content-type': 'application/json',
        'digest': f'SHA-256={digest}',
        'origin': 'https://www.wenxiaobai.com',
        'priority': 'u=1, i',
        'referer': 'https://www.wenxiaobai.com/',
        'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
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


def is_thinking_content(content: str) -> bool:
    """Check if content is thinking process"""
    return "```ys_think" in content


def clean_thinking_content(content: str) -> str:
    """Clean thinking content, remove special markers"""
    # Remove thinking blocks
    if "```ys_think" in content:
        cleaned = re.sub(r'```ys_think.*?```', '', content, flags=re.DOTALL)
        if cleaned and cleaned.strip():
            return cleaned.strip()
        return ""
    return content


# New function to clean reference artifacts
def clean_reference_artifacts(content: str) -> str:
    """Remove reference artifacts like [这是一个数字](@ref) from content"""
    # Pattern to match references like [text](@ref)
    pattern = r'\[[^\]]+\]$$@ref$$'
    cleaned = re.sub(pattern, '', content)
    return cleaned


# Verify API key
async def verify_api_key(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing API key")

    api_key = authorization.replace("Bearer ", "").strip()
    if api_key != Config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


def create_chunk(sse_id: str, created: int, content: Optional[str] = None,
                 is_first: bool = False, meta: Optional[dict] = None,
                 finish_reason: Optional[str] = None) -> dict:
    """Create response chunk"""
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
    """Process message event"""
    content = data.get("content", "")
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))
    result = ""

    # Clean reference artifacts
    content = clean_reference_artifacts(content)

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
    """Process generation end event"""
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


# Improved error handling with retries
async def with_retries(func, *args, **kwargs):
    """Execute a function with retries"""
    retries = 0
    last_error = None
    
    while retries <= Config.MAX_RETRIES:
        try:
            return await func(*args, **kwargs)
        except (httpx.RequestError, HTTPException) as e:
            last_error = e
            retries += 1
            if retries > Config.MAX_RETRIES:
                break
            
            # Exponential backoff
            wait_time = Config.RETRY_BACKOFF_FACTOR * (2 ** (retries - 1))
            logger.warning(f"Retry {retries}/{Config.MAX_RETRIES} after error: {e}. Waiting {wait_time}s")
            await asyncio.sleep(wait_time)
    
    # If we get here, all retries failed
    logger.error(f"All retries failed: {last_error}")
    if isinstance(last_error, HTTPException):
        raise last_error
    raise HTTPException(status_code=500, detail=f"Request failed after {Config.MAX_RETRIES} retries: {str(last_error)}")


async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0) -> AsyncGenerator[str, None]:
    """Generate response with true streaming and improved error handling"""
    # Ensure session is initialized
    await session_manager.refresh_if_needed()

    timestamp = generate_timestamp()
    
    # Extract context from previous messages
    context = []
    for msg in messages[:-1]:  # All messages except the last one
        if msg['role'] in ['user', 'assistant']:
            context.append({
                'role': 'user' if msg['role'] == 'user' else 'bot',
                'content': msg['content']
            })
    
    # Prepare payload with context
    payload = {
        'userId': session_manager.user_id,
        'botId': Config.BOT_ID,
        'botAlias': 'custom',
        'query': messages[-1]['content'],
        'isRetry': False,
        'breakingStrategy': 0,
        'isNewConversation': len(context) == 0,  # Only new if no context
        'mediaInfos': [],
        'turnIndex': len(context) // 2,  # Each turn has user + assistant
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
        'context': context  # Add context from previous messages
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

    # Get client from session manager
    client = await session_manager.get_client()
    
    # Process streaming response with improved error handling
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
            empty_response_count = 0
            max_empty_responses = 5
            last_activity = time.time()
            timeout = 60  # seconds

            async for line in response.aiter_lines():
                # Reset timeout counter on activity
                last_activity = time.time()
                
                line = line.strip()
                if not line:
                    current_event = None
                    empty_response_count += 1
                    
                    # If too many empty responses, yield a heartbeat to keep connection alive
                    if empty_response_count >= max_empty_responses:
                        empty_response_count = 0
                        yield f"data: {json.dumps({'heartbeat': True})}\n\n"
                    
                    continue

                # Reset empty response counter on valid data
                empty_response_count = 0

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
                
                # Check for timeout
                if time.time() - last_activity > timeout:
                    logger.warning("Response stream timed out")
                    # Send end marker on timeout
                    for chunk in process_generate_end_event(
                        {"timestamp": str(int(time.time() * 1000))}, 
                        in_thinking_block, 
                        thinking_content
                    ):
                        yield chunk
                    break

    except httpx.RequestError as e:
        logger.error(f"Generate response error: {e}")
        # Try to reinitialize session
        try:
            await session_manager.initialize_async()
            logger.info("Session reinitialized")
        except Exception as re_init_error:
            logger.error(f"Session reinitialization failed: {re_init_error}")
        
        # Send error response in streaming format
        error_id = str(uuid.uuid4())
        error_time = int(time.time())
        error_chunk = create_chunk(
            sse_id=error_id,
            created=error_time,
            content="I apologize, but I encountered an error processing your request. Please try again.",
            is_first=True
        )
        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
        
        end_chunk = create_chunk(
            sse_id=error_id,
            created=error_time,
            finish_reason="error"
        )
        yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


@app.get("/")
async def hff():
    return {"status": "ok", "message": "hefengfan API successfully deployed!"}


@app.get("/v1/models")
async def list_models():
    """List available models"""
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
async def chat_completions(request: ChatCompletionRequest, authorization: str = Header(None), background_tasks: BackgroundTasks):
    """Handle chat completion requests with improved memory management"""
    # Verify API key
    await verify_api_key(authorization)

    # Log request (minimal logging to save memory)
    logger.info(f"Chat request: model={request.model}, stream={request.stream}")
    messages = [msg.model_dump() for msg in request.messages]

    if not request.stream:
        # Non-streaming response handling
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
                logger.error(f"Non-streaming response processing error: {e}")

        # Clean reference artifacts in final content
        content = clean_reference_artifacts(content)

        # Build complete response
        response = {
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
        
        # Schedule garbage collection after request
        background_tasks.add_task(gc.collect)
        
        return response

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
    """Health check endpoint with memory usage info"""
    import psutil
    
    process = psutil.Process()
    memory_info = process.memory_info()
    memory_mb = memory_info.rss / (1024 * 1024)
    
    status = "ok" if session_manager.is_initialized() else "degraded"
    session_status = "active" if session_manager.is_initialized() else "inactive"
    
    return {
        "status": status, 
        "session": session_status,
        "memory_usage_mb": round(memory_mb, 2),
        "uptime_seconds": int(time.time() - process.create_time())
    }

