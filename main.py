import asyncio
import json
import random
import uuid
import datetime
import time
import re
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
import gc

# Configure logging with minimal overhead
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Track last API call time for garbage collection
last_api_call_time = time.time()
gc_interval = 300  # 5 minutes

# Use lifespan for more efficient resource management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize resources on startup
    client = httpx.AsyncClient(timeout=httpx.Timeout(150), limits=httpx.Limits(max_keepalive_connections=5, max_connections=10))
    app.state.http_client = client
    
    # Start background garbage collection task
    app.state.gc_task = asyncio.create_task(periodic_garbage_collection(app))
    
    # Initialize session on startup
    try:
        await session_manager.refresh_if_needed(client)
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
    
    yield
    
    # Clean up resources on shutdown
    app.state.gc_task.cancel()
    try:
        await app.state.gc_task
    except asyncio.CancelledError:
        pass
    
    await client.aclose()
    gc.collect()  # Force garbage collection

# Periodic garbage collection function
async def periodic_garbage_collection(app):
    """Background task to periodically clean up memory when idle"""
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute
            current_time = time.time()
            global last_api_call_time
            
            # If no API calls for gc_interval seconds, run garbage collection
            if current_time - last_api_call_time > gc_interval:
                logger.info("Server idle, running garbage collection")
                gc.collect()
                
                # Also close and recreate HTTP client if it's been idle too long
                if hasattr(app.state, 'http_client'):
                    old_client = app.state.http_client
                    app.state.http_client = httpx.AsyncClient(
                        timeout=httpx.Timeout(150), 
                        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                    )
                    await old_client.aclose()
        except Exception as e:
            logger.error(f"Error in garbage collection task: {e}")
            await asyncio.sleep(60)  # Wait before retrying

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

# Optimized session manager with minimal state
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None
        self.last_refresh = 0
        self.refresh_interval = 3600  # Refresh token every hour

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    async def refresh_if_needed(self, client=None):
        """Refresh session if needed"""
        current_time = time.time()
        
        # Check if refresh is needed
        if (not self.is_initialized() or 
            current_time - self.last_refresh > self.refresh_interval):
            await self.initialize(client)
            self.last_refresh = current_time

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
        logger.error(f"Failed to get auth token: {e}")
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

    try:
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
    """Check if content is thinking process"""
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

def clean_reference_tags(content: str) -> str:
    """Remove reference tags like [这是一个数字](@ref) from content"""
    # Remove patterns like [text](@ref)
    cleaned = re.sub(r'\[[^\]]*\]$$@ref$$', '', content)
    return cleaned

# Optimized response chunk creation
def create_chunk(sse_id: str, created: int, content: Optional[str] = None,
                 is_first: bool = False, meta: Optional[dict] = None,
                 finish_reason: Optional[str] = None) -> dict:
    """Create response chunk with minimal overhead"""
    delta = {}

    if content is not None:
        # Clean reference tags from content
        if content:
            content = clean_reference_tags(content)
            
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

    # Clean reference tags from content
    content = clean_reference_tags(content)

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
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
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
            content="\n<Thinking>\n</Thinking>\n\n"
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

# Format conversation history for better context understanding
def format_conversation_history(messages: List[dict]) -> str:
    """Format the conversation history to improve context understanding"""
    formatted_history = []
    
    for msg in messages:
        role = msg.get('role', '')
        content = msg.get('content', '')
        
        if role == 'system':
            formatted_history.append(f"System: {content}")
        elif role == 'user':
            formatted_history.append(f"User: {content}")
        elif role == 'assistant':
            formatted_history.append(f"Assistant: {content}")
    
    # Get the last user message separately (will be used as the main query)
    last_user_message = ""
    for msg in reversed(messages):
        if msg.get('role') == 'user':
            last_user_message = msg.get('content', '')
            break
    
    # Return both the formatted history and the last user message
    return "\n\n".join(formatted_history), last_user_message

# Optimized response generation
async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0, 
                            client=None) -> AsyncGenerator[str, None]:
    """Generate response with true streaming and minimal memory usage"""
    # Update last API call time for garbage collection
    global last_api_call_time
    last_api_call_time = time.time()
    
    # Ensure session is initialized
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(150))
        close_client = True
    
    try:
        await session_manager.refresh_if_needed(client)

        # Format conversation history for better context understanding
        conversation_history, last_query = format_conversation_history(messages)
        
        timestamp = generate_timestamp()
        payload = {
            'userId': session_manager.user_id,
            'botId': Config.BOT_ID,
            'botAlias': 'custom',
            'query': last_query,  # Use the last user message as the main query
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
                    'BotId': 210029,
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
            # Add conversation history to help with context
            'conversationHistory': conversation_history
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

            async for raw_line in response.aiter_bytes(1024):
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
                                    yield result

                            # Process generation end event
                            elif current_event == "generateEnd":
                                for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                    yield chunk

                        except json.JSONDecodeError as e:
                            logger.error(f"JSON parsing error: {e}")
                            continue
    except httpx.RequestError as e:
        logger.error(f"Generate response error: {e}")
        # Try to reinitialize session
        try:
            await session_manager.initialize(client)
            logger.info("Session reinitialized")
        except Exception as re_init_error:
            logger.error(f"Failed to reinitialize session: {re_init_error}")
        raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")
    finally:
        if close_client:
            await client.aclose()

# API endpoints
@app.get("/")
async def health(background_tasks: BackgroundTasks):
    # Run garbage collection in the background
    background_tasks.add_task(gc.collect)
    return {"status": "ok", "message": "hefengfan API successfully deployed!"}

@app.get("/v1/models")
async def list_models():
    """List available models"""
    # Update last API call time
    global last_api_call_time
    last_api_call_time = time.time()
    
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
async def chat_completions(request: ChatCompletionRequest, authorization: str = Header(None), req: Request = None):
    """Process chat completion requests with memory optimization"""
    # Update last API call time
    global last_api_call_time
    last_api_call_time = time.time()
    
    # Verify API key
    await verify_api_key(authorization)

    # Add request log
    logger.info(f"Received chat request: model={request.model}, stream={request.stream}")
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

        # Clean any reference tags from final content
        content = clean_reference_tags(content)
        
        # Build complete response
        return {
            "id": str(uuid.uuid4()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{
                "message": {
                    "role": "assistant",
                    "reasoning_content": f"<Thinking>\n{thinking_content}\n</Thinking>" if thinking_content else None,
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
    """Health check endpoint with garbage collection"""
    # Run garbage collection in the background
    background_tasks.add_task(gc.collect)
    
    if session_manager.is_initialized():
        return {"status": "ok", "session": "active", "last_api_call": time.time() - last_api_call_time}
    else:
        return {"status": "degraded", "session": "inactive", "last_api_call": time.time() - last_api_call_time}

@app.get("/gc")
async def force_garbage_collection():
    """Force garbage collection"""
    before = gc.get_count()
    collected = gc.collect(generation=2)
    after = gc.get_count()
    
    return {
        "status": "ok", 
        "collected": collected,
        "before": before,
        "after": after,
        "last_api_call": time.time() - last_api_call_time
    }
