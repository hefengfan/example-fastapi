import asyncio
import json
import random
import uuid
import datetime
import time
import re
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple
import httpx
import logging
import hashlib
import base64
import hmac
import gc
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool

# Configure logging with memory-efficient settings
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Create FastAPI app with optimized settings
app = FastAPI(
    title="AI Chat API",
    description="Optimized API for low-memory environments",
    docs_url=None,  # Disable Swagger UI to save memory
    redoc_url=None  # Disable ReDoc to save memory
)

# Memory optimization middleware
class MemoryOptimizationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Force garbage collection after each request
        await run_in_threadpool(gc.collect)
        return response

app.add_middleware(MemoryOptimizationMiddleware)

# Add CORS middleware with memory-efficient settings
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration class with memory-efficient design
class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"
    # Add timeout and retry settings
    REQUEST_TIMEOUT = 60
    MAX_RETRIES = 3
    RETRY_DELAY = 2

# Session manager with improved error handling
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None
        self.last_error = None
        self.error_count = 0
        self.last_success = 0

    def initialize(self):
        """Initialize session with error handling"""
        try:
            self.device_id = generate_device_id()
            self.token, self.user_id = get_auth_token(self.device_id)
            self.conversation_id = create_conversation(self.device_id, self.token, self.user_id)
            self.last_error = None
            self.error_count = 0
            self.last_success = time.time()
            logger.info(f"Session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")
            return True
        except Exception as e:
            self.last_error = str(e)
            self.error_count += 1
            logger.error(f"Session initialization failed: {e}")
            return False

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    def needs_refresh(self):
        """Check if session needs refresh based on time or errors"""
        if not self.is_initialized():
            return True
        if self.error_count >= 3:
            return True
        if time.time() - self.last_success > 3600:  # Refresh after 1 hour
            return True
        return False

    async def refresh_if_needed(self):
        """Refresh session if needed with backoff strategy"""
        if self.needs_refresh():
            retry_count = 0
            while retry_count < Config.MAX_RETRIES:
                if self.initialize():
                    return True
                retry_count += 1
                await asyncio.sleep(Config.RETRY_DELAY * (2 ** retry_count))  # Exponential backoff
            return False
        return True

# Create session manager instance
session_manager = SessionManager()

# Pydantic models with memory-efficient design
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

# Utility functions
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

def get_auth_token(device_id: str) -> Tuple[str, str]:
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
        with httpx.Client(timeout=Config.REQUEST_TIMEOUT) as client:
            response = client.post(
                f"{Config.BASE_URL}/user/sessions",
                headers=headers,
                content=data
            )
            response.raise_for_status()
            result = response.json()
            return result['data']['token'], result['data']['user']['id']
    except httpx.RequestError as e:
        logger.error(f"Authentication token request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Authentication response parsing failed: {e}")
        raise HTTPException(status_code=500, detail="Server returned invalid authentication data")

def create_conversation(device_id: str, token: str, user_id: str) -> str:
    """Create new conversation"""
    timestamp = generate_timestamp()
    payload = {'visitorId': device_id}
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)

    headers = create_common_headers(timestamp, digest, token, device_id)

    try:
        with httpx.Client(timeout=Config.REQUEST_TIMEOUT) as client:
            response = client.post(
                f"{Config.BASE_URL}/core/conversations/users/{user_id}/bots/{Config.BOT_ID}/conversation",
                headers=headers,
                content=data
            )
            response.raise_for_status()
            return response.json()['data']
    except httpx.RequestError as e:
        logger.error(f"Create conversation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Create conversation failed: {str(e)}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Conversation response parsing failed: {e}")
        raise HTTPException(status_code=500, detail="Server returned invalid conversation data")

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

# Function to remove reference patterns like [1](@ref)
def remove_reference_patterns(content: str) -> str:
    """Remove reference patterns like [数字](@ref) from content"""
    # Pattern to match [数字](@ref) or similar reference patterns
    pattern = r'\[\d+\]$$@ref$$'
    return re.sub(pattern, '', content)

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

    # Remove reference patterns from content
    content = remove_reference_patterns(content)

    # Check if it's the start of thinking block
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
        # Fix the syntax error in the original code
        result = f"data: \{json.dumps(chunk, ensure_ascii=False)\}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Check if it's the end of thinking block
    if "```" in content and in_thinking_block:
        in_thinking_block = False
        # Send thinking block end marker
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="\n</Thinking>\n\n"
        )
        # Fix the syntax error in the original code
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
        # Fix the syntax error in the original code
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Clean content, remove thinking block
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
    # Fix the syntax error in the original code
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
        # Fix the syntax error in the original code
        result.append(f"data: {json.dumps(end_thinking_chunk, ensure_ascii=False)}\n\n")

    # Add metadata
    meta_chunk = create_chunk(
        sse_id=sse_id,
        created=created,
        meta={"thinking_content": "".join(thinking_content) if thinking_content else None}
    )
    # Fix the syntax error in the original code
    result.append(f"data: {json.dumps(meta_chunk, ensure_ascii=False)}\n\n")

    # Send end marker
    end_chunk = create_chunk(
        sse_id=sse_id,
        created=created,
        finish_reason="stop"
    )
    # Fix the syntax error in the original code
    result.append(f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n")
    result.append("data: [DONE]\n\n")
    return result

# Memory-efficient generator for streaming responses
async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0) -> AsyncGenerator[str, None]:
    """Generate response - using true streaming with memory optimization"""
    # Ensure session is initialized
    session_initialized = await session_manager.refresh_if_needed()
    if not session_initialized:
        # If session initialization fails, yield an error message
        error_chunk = create_chunk(
            sse_id=str(uuid.uuid4()),
            created=int(time.time()),
            content="服务暂时不可用，请稍后再试。",
            is_first=True
        )
        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
        
        end_chunk = create_chunk(
            sse_id=str(uuid.uuid4()),
            created=int(time.time()),
            finish_reason="error"
        )
        yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    timestamp = generate_timestamp()
    payload = {
        'userId': session_manager.user_id,
        'botId': Config.BOT_ID,
        'botAlias': 'custom',
        'query': messages[-1]['content'],
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

    # Track empty response handling
    empty_response_count = 0
    max_empty_responses = 3
    retry_count = 0
    max_retries = Config.MAX_RETRIES

    while retry_count <= max_retries:
        try:
            # Use stream=True parameter for true streaming
            async with httpx.AsyncClient(timeout=httpx.Timeout(Config.REQUEST_TIMEOUT)) as client:
                async with client.stream('POST', f"{Config.BASE_URL}/core/conversation/chat/v1",
                                        headers=headers, content=data) as response:
                    response.raise_for_status()

                    # Process streaming response
                    is_first_chunk = True
                    current_event = None
                    in_thinking_block = False
                    thinking_content = []
                    thinking_started = False
                    has_yielded_content = False

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
                                    if result:
                                        has_yielded_content = True
                                        yield result

                                # Process generation end event
                                elif current_event == "generateEnd":
                                    for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                        has_yielded_content = True
                                        yield chunk

                            except json.JSONDecodeError as e:
                                logger.error(f"JSON parsing error: {e}")
                                continue

                    # Handle empty response
                    if not has_yielded_content:
                        empty_response_count += 1
                        if empty_response_count >= max_empty_responses:
                            # If we've had too many empty responses, yield an error
                            error_chunk = create_chunk(
                                sse_id=str(uuid.uuid4()),
                                created=int(time.time()),
                                content="服务暂时不可用，请稍后再试。",
                                is_first=True
                            )
                            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                            
                            end_chunk = create_chunk(
                                sse_id=str(uuid.uuid4()),
                                created=int(time.time()),
                                finish_reason="error"
                            )
                            yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
                            yield "data: [DONE]\n\n"
                            break
                        
                        # Try to reinitialize session
                        session_manager.initialize()
                        retry_count += 1
                        await asyncio.sleep(Config.RETRY_DELAY * (2 ** retry_count))  # Exponential backoff
                        continue
                    
                    # If we got here with content, we're done
                    session_manager.last_success = time.time()
                    session_manager.error_count = 0
                    break

        except httpx.RequestError as e:
            logger.error(f"Generate response error: {e}")
            retry_count += 1
            
            # Try to reinitialize session
            try:
                session_manager.initialize()
                logger.info("Session reinitialized")
            except Exception as re_init_error:
                logger.error(f"Session reinitialization failed: {re_init_error}")
            
            # If we've reached max retries, yield an error
            if retry_count > max_retries:
                error_chunk = create_chunk(
                    sse_id=str(uuid.uuid4()),
                    created=int(time.time()),
                    content="服务暂时不可用，请稍后再试。",
                    is_first=True
                )
                yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                
                end_chunk = create_chunk(
                    sse_id=str(uuid.uuid4()),
                    created=int(time.time()),
                    finish_reason="error"
                )
                yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                break
            
            await asyncio.sleep(Config.RETRY_DELAY * (2 ** retry_count))  # Exponential backoff

@app.get("/")
async def hff():
    return {"status": "ok", "提示": "hefengfan接口已成功部署！"}

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
async def chat_completions(request: ChatCompletionRequest, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    """Handle chat completion requests with memory optimization"""
    # Verify API key
    await verify_api_key(authorization)

    # Add request log
    logger.info(f"Received chat request: model={request.model}, stream={request.stream}")
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
                            # Remove reference patterns
                            content_part = remove_reference_patterns(content_part)

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

        # Build complete response
        response_data = {
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
        
        # Schedule garbage collection after response
        background_tasks.add_task(gc.collect)
        
        return response_data

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

@app.on_event("startup")
async def startup_event():
    """Initialize session on application startup"""
    try:
        # Set lower memory limits for httpx
        httpx._config.DEFAULT_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        
        # Initialize session
        session_manager.initialize()
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
        # Don't raise here, allow the app to start anyway

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    memory_info = {}
    try:
        import psutil
        process = psutil.Process()
        memory_info = {
            "memory_usage_mb": process.memory_info().rss / (1024 * 1024),
            "memory_percent": process.memory_percent()
        }
    except ImportError:
        memory_info = {"note": "psutil not installed, memory stats unavailable"}
    
    if session_manager.is_initialized():
        return {
            "status": "ok", 
            "session": "active", 
            "last_success": session_manager.last_success,
            "memory": memory_info
        }
    else:
        return {
            "status": "degraded", 
            "session": "inactive", 
            "last_error": session_manager.last_error,
            "memory": memory_info
        }

# Add a route to force garbage collection
@app.post("/admin/gc")
async def force_gc(authorization: str = Header(None)):
    """Force garbage collection"""
    await verify_api_key(authorization)
    collected = gc.collect()
    return {"status": "ok", "collected": collected}

# Add a route to reset session
@app.post("/admin/reset-session")
async def reset_session(authorization: str = Header(None)):
    """Reset session"""
    await verify_api_key(authorization)
    success = session_manager.initialize()
    return {"status": "ok" if success else "error", "session_initialized": success}
