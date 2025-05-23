import asyncio
import json
import random
import uuid
import datetime
import time
import re
from fastapi import FastAPI, HTTPException, Header, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple
import httpx
import logging
import hashlib
import base64
import hmac
from fastapi.middleware.cors import CORSMiddleware
from cachetools import TTLCache, LRUCache
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache configuration
# Using TTLCache for time-based expiration and LRUCache for memory management
TOKEN_CACHE = TTLCache(maxsize=100, ttl=3600)  # Cache tokens for 1 hour
CONVERSATION_CACHE = LRUCache(maxsize=500)  # Limit to 500 conversations
RESPONSE_CACHE = TTLCache(maxsize=200, ttl=300)  # Cache responses for 5 minutes

# Lifespan context manager for startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources on startup
    try:
        # Create a global httpx client for reuse
        app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(150))
        logger.info("Application started, HTTP client initialized")
        yield
    finally:
        # Clean up resources on shutdown
        await app.state.http_client.aclose()
        logger.info("Application shutting down, resources cleaned up")

# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins, should be more restrictive in production
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

# Configuration class for API settings
class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"
    # Add cache settings
    CACHE_ENABLED = True
    CACHE_TTL = 300  # 5 minutes
    MAX_CONNECTIONS = 100  # Limit concurrent connections

# Session manager with improved caching
class SessionManager:
    def __init__(self):
        self._sessions = {}
        self._lock = asyncio.Lock()
        
    async def get_session(self, session_id: str = None):
        """Get or create a session with caching"""
        session_id = session_id or str(uuid.uuid4())
        
        # Check cache first
        if session_id in self._sessions:
            session = self._sessions[session_id]
            # Check if session is still valid
            if session.get("expires_at", 0) > time.time():
                return session
        
        # Create new session
        async with self._lock:  # Prevent race conditions
            device_id = generate_device_id()
            
            # Check token cache
            cache_key = f"token:{device_id}"
            if cache_key in TOKEN_CACHE:
                token, user_id = TOKEN_CACHE[cache_key]
            else:
                token, user_id = await get_auth_token(device_id)
                TOKEN_CACHE[cache_key] = (token, user_id)
            
            # Check conversation cache
            conv_cache_key = f"conv:{user_id}"
            if conv_cache_key in CONVERSATION_CACHE:
                conversation_id = CONVERSATION_CACHE[conv_cache_key]
            else:
                conversation_id = await create_conversation(device_id, token, user_id)
                CONVERSATION_CACHE[conv_cache_key] = conversation_id
            
            # Create session with expiration
            session = {
                "device_id": device_id,
                "token": token,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "created_at": time.time(),
                "expires_at": time.time() + 3600,  # 1 hour expiration
                "id": session_id
            }
            self._sessions[session_id] = session
            
            # Clean up old sessions periodically
            if len(self._sessions) > 1000:  # Arbitrary limit
                self._cleanup_sessions()
                
            return session
    
    def _cleanup_sessions(self):
        """Remove expired sessions"""
        current_time = time.time()
        expired_keys = [k for k, v in self._sessions.items() 
                       if v.get("expires_at", 0) < current_time]
        for key in expired_keys:
            del self._sessions[key]

# Create session manager instance
session_manager = SessionManager()

# Pydantic models for request/response validation
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

# Helper functions
def generate_device_id() -> str:
    """Generate a unique device ID"""
    return f"{uuid.uuid4().hex}_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"

def generate_timestamp() -> str:
    """Generate UTC timestamp in required format"""
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

# Async API functions
async def get_auth_token(device_id: str) -> Tuple[str, str]:
    """Get authentication token"""
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
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
    """Create a new conversation"""
    timestamp = generate_timestamp()
    payload = {'visitorId': device_id}
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)

    headers = create_common_headers(timestamp, digest, token, device_id)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
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

# Content processing functions
def is_thinking_content(content: str) -> bool:
    """Check if content is thinking process"""
    return "```ys_think" in content

def clean_thinking_content(content: str) -> str:
    """Clean thinking process content, remove special markers"""
    if "```ys_think" in content:
        cleaned = re.sub(r'```ys_think.*?```', '', content, flags=re.DOTALL)
        if cleaned and cleaned.strip():
            return cleaned.strip()
        return ""
    return content

# Dependency for API key verification
async def verify_api_key(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing API key")

    api_key = authorization.replace("Bearer ", "").strip()
    if api_key != Config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key

# Dependency to get client IP for rate limiting
async def get_client_ip(request: Request):
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0]
    return request.client.host

# Response chunk creation
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

# Message event processing
async def process_message_event(data: dict, is_first_chunk: bool, in_thinking_block: bool,
                                thinking_started: bool, thinking_content: list) -> Tuple[str, bool, bool, bool, list]:
    """Process message event"""
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

# Generate end event processing
def process_generate_end_event(data: dict, in_thinking_block: bool, thinking_content: list) -> List[str]:
    """Process generate end event"""
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

# Response generation with improved streaming
async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0, 
                            client_ip: str = None) -> AsyncGenerator[str, None]:
    """Generate response with true streaming"""
    # Get session
    session = await session_manager.get_session()
    
    # Check cache for identical requests if caching is enabled
    if Config.CACHE_ENABLED and not stream:
        cache_key = hashlib.md5(
            json.dumps({
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "presence_penalty": presence_penalty,
                "frequency_penalty": frequency_penalty,
                "top_p": top_p
            }).encode()
        ).hexdigest()
        
        if cache_key in RESPONSE_CACHE:
            logger.info(f"Cache hit for request {cache_key[:8]}")
            cached_response = RESPONSE_CACHE[cache_key]
            # For non-streaming requests, we can return the cached full response
            if not stream:
                yield json.dumps(cached_response)
                return

    timestamp = generate_timestamp()
    payload = {
        'userId': session["user_id"],
        'botId': Config.BOT_ID,
        'botAlias': 'custom',
        'query': messages[-1]['content'],
        'isRetry': False,
        'breakingStrategy': 0,
        'isNewConversation': True,
        'mediaInfos': [],
        'turnIndex': 0,
        'rewriteQuery': '',
        'conversationId': session["conversation_id"],
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
    headers = create_common_headers(timestamp, digest, session["token"], session["device_id"])
    headers.update({
        'accept': 'text/event-stream, text/event-stream',
        'x-yuanshi-appversioncode': '',
        'x-yuanshi-appversionname': '3.1.0',
    })

    try:
        # Use app's shared HTTP client for better connection reuse
        async with app.state.http_client.stream('POST', f"{Config.BASE_URL}/core/conversation/chat/v1",
                                 headers=headers, content=data) as response:
            response.raise_for_status()

            # Process streaming response
            is_first_chunk = True
            current_event = None
            in_thinking_block = False
            thinking_content = []
            thinking_started = False
            full_response = {"content": "", "thinking_content": ""}

            async for line in response.aiter_lines():
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
                            
                            # For caching, collect the full response
                            if Config.CACHE_ENABLED and not stream:
                                if "choices" in json.loads(result[6:]):
                                    chunk_data = json.loads(result[6:])
                                    if "delta" in chunk_data["choices"][0]:
                                        delta = chunk_data["choices"][0]["delta"]
                                        if "content" in delta:
                                            if in_thinking_block:
                                                full_response["thinking_content"] += delta["content"]
                                            else:
                                                full_response["content"] += delta["content"]
                            
                            if result:
                                yield result

                        # Process generate end event
                        elif current_event == "generateEnd":
                            for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                yield chunk
                                
                            # Cache the full response for non-streaming requests
                            if Config.CACHE_ENABLED and not stream:
                                cached_response = {
                                    "id": str(uuid.uuid4()),
                                    "object": "chat.completion",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [{
                                        "message": {
                                            "role": "assistant",
                                            "reasoning_content": f"<Thinking>\n\{full_response['thinking_content']\}\n</Thinking>" if full_response["thinking_content"] else None,
                                            "content": full_response["content"],
                                        },
                                        "finish_reason": "stop"
                                    }]
                                }
                                RESPONSE_CACHE[cache_key] = cached_response

                    except json.JSONDecodeError as e:
                        logger.error(f"JSON parsing error: {e}")
                        continue

    except httpx.RequestError as e:
        logger.error(f"Error generating response: {e}")
        # Try to reinitialize session
        try:
            await session_manager.get_session(None)  # Force new session
            logger.info("Session reinitialized")
        except Exception as re_init_error:
            logger.error(f"Failed to reinitialize session: {re_init_error}")
        raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")

# API endpoints
@app.get("/")
async def root():
    return {"status": "ok", "message": "hefengfan API successfully deployed!"}

@app.get("/v1/models")
async def list_models(authorization: str = Header(None)):
    """List available models"""
    await verify_api_key(authorization)
    
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
    client_ip: str = Depends(get_client_ip)
):
    """Handle chat completion requests"""
    # Verify API key
    await verify_api_key(authorization)

    # Add request log
    logger.info(f"Received chat request: model={request.model}, stream={request.stream}, client={client_ip}")
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
                top_p=request.top_p,
                client_ip=client_ip
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
            top_p=request.top_p,
            client_ip=client_ip
        ),
        media_type="text/event-stream"
    )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test if we can create a session
        session = await session_manager.get_session()
        return {
            "status": "ok", 
            "session": "active",
            "cache_stats": {
                "token_cache": len(TOKEN_CACHE),
                "conversation_cache": len(CONVERSATION_CACHE),
                "response_cache": len(RESPONSE_CACHE)
            }
        }
    except Exception as e:
        return {"status": "degraded", "session": "inactive", "error": str(e)}

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
