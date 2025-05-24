import asyncio
import json
import random
import uuid
import datetime
import time
import re
import gc
import threading
import os
import sys
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple
import httpx
import logging
import hashlib
import base64
import hmac

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI instance
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enhanced Memory management configuration
class MemoryManager:
    def __init__(self, threshold_percent=75, check_interval=30, auto_cleanup_interval=300):
        self.threshold_percent = threshold_percent
        self.check_interval = check_interval
        self.auto_cleanup_interval = auto_cleanup_interval  # Auto cleanup every 5 minutes
        self.is_running = False
        self.monitor_thread = None
        self.auto_cleanup_thread = None
        self.last_cleanup = time.time()
        
    def start_monitoring(self):
        """Start the memory monitoring and auto cleanup threads"""
        if not self.is_running:
            self.is_running = True
            # Start memory monitoring thread
            self.monitor_thread = threading.Thread(target=self._monitor_memory, daemon=True)
            self.monitor_thread.start()
            # Start auto cleanup thread
            self.auto_cleanup_thread = threading.Thread(target=self._auto_cleanup, daemon=True)
            self.auto_cleanup_thread.start()
            logger.info("Memory monitoring and auto cleanup started")
    
    def _auto_cleanup(self):
        """Periodic automatic memory cleanup"""
        while self.is_running:
            try:
                time.sleep(self.auto_cleanup_interval)
                current_time = time.time()
                if current_time - self.last_cleanup >= self.auto_cleanup_interval:
                    logger.info("Performing scheduled memory cleanup")
                    self.cleanup_memory()
                    self.last_cleanup = current_time
            except Exception as e:
                logger.error(f"Error in auto cleanup: {e}")
    
    def _monitor_memory(self):
        """Monitor memory usage and trigger cleanup when necessary"""
        while self.is_running:
            try:
                memory_percent = self.get_memory_usage()
                
                if memory_percent > self.threshold_percent:
                    logger.warning(f"Memory usage high ({memory_percent:.1f}%), triggering cleanup")
                    self.cleanup_memory()
                    
            except Exception as e:
                logger.error(f"Error in memory monitoring: {e}")
                
            time.sleep(self.check_interval)
    
    def get_memory_usage(self):
        """Get current memory usage percentage"""
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            if sys.platform == 'darwin':
                mem_used = usage.ru_maxrss / 1024 / 1024
            else:
                mem_used = usage.ru_maxrss / 1024
                
            total_mem = self._get_total_memory()
            if total_mem > 0:
                return (mem_used / total_mem) * 100
            
            return (mem_used / 500) * 100
            
        except ImportError:
            gc.collect()
            obj_count = len(gc.get_objects())
            threshold = 1000000
            return (obj_count / threshold) * 100
    
    def _get_total_memory(self):
        """Get total system memory in MB"""
        try:
            if os.path.exists('/proc/meminfo'):
                with open('/proc/meminfo', 'r') as f:
                    for line in f:
                        if 'MemTotal' in line:
                            return int(line.split()[1]) / 1024
            return 0
        except:
            return 0
    
    def cleanup_memory(self):
        """Enhanced memory cleanup operations"""
        # Force garbage collection for all generations
        collected = gc.collect()
        logger.info(f"Garbage collection: collected {collected} objects")
        
        # Clear httpx cache
        try:
            httpx._pools.POOLS.clear()
            logger.info("Cleared httpx connection pools")
        except:
            pass
        
        # Clear session cache if exists
        try:
            if hasattr(session_manager, '_cache'):
                session_manager._cache.clear()
        except:
            pass
        
        # System-level memory optimization
        try:
            import ctypes
            ctypes.CDLL('libc.so.6').malloc_trim(0)
            logger.info("Called malloc_trim to release memory to OS")
        except:
            pass
        
        memory_percent = self.get_memory_usage()
        logger.info(f"Memory after cleanup: {memory_percent:.1f}%")

# Initialize enhanced memory manager
memory_manager = MemoryManager(threshold_percent=70, check_interval=20, auto_cleanup_interval=180)

class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"

# Enhanced session management with context support
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None
        self.conversation_history = []  # Store conversation context
        self.max_history = 20  # Keep last 20 messages for context
        self._cache = {}  # Session cache
        
    def add_to_history(self, role: str, content: str):
        """Add message to conversation history"""
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        
        # Keep only recent messages
        if len(self.conversation_history) > self.max_history:
            self.conversation_history = self.conversation_history[-self.max_history:]
    
    def get_context_messages(self, current_messages: List[dict]) -> List[dict]:
        """Get messages with context from history"""
        # Combine history with current messages, avoiding duplicates
        context_messages = []
        
        # Add recent history (excluding the last message to avoid duplication)
        for msg in self.conversation_history[:-1]:
            context_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Add current messages
        context_messages.extend(current_messages)
        
        return context_messages

    def initialize(self):
        """Initialize session with caching"""
        cache_key = "session_data"
        
        # Try to use cached session if recent
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if time.time() - cached_data.get('timestamp', 0) < 3600:  # 1 hour cache
                self.device_id = cached_data['device_id']
                self.token = cached_data['token']
                self.user_id = cached_data['user_id']
                self.conversation_id = cached_data['conversation_id']
                logger.info("Using cached session data")
                return
        
        # Create new session
        self.device_id = generate_device_id()
        self.token, self.user_id = get_auth_token(self.device_id)
        self.conversation_id = create_conversation(self.device_id, self.token, self.user_id)
        
        # Cache session data
        self._cache[cache_key] = {
            'device_id': self.device_id,
            'token': self.token,
            'user_id': self.user_id,
            'conversation_id': self.conversation_id,
            'timestamp': time.time()
        }
        
        logger.info(f"New session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    async def refresh_if_needed(self):
        """Refresh session if needed"""
        if not self.is_initialized():
            self.initialize()

# Create enhanced session manager
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
    """Generate UTC time string as required"""
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
        response = httpx.post(
            f"{Config.BASE_URL}/user/sessions",
            headers=headers,
            content=data,
            timeout=30  # Reduced timeout for faster response
        )
        response.raise_for_status()
        result = response.json()
        return result['data']['token'], result['data']['user']['id']
    except httpx.RequestError as e:
        logger.error(f"Failed to get auth token: {e}")
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")

def create_conversation(device_id: str, token: str, user_id: str) -> str:
    """Create new conversation"""
    timestamp = generate_timestamp()
    payload = {'visitorId': device_id}
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)
    headers = create_common_headers(timestamp, digest, token, device_id)

    try:
        response = httpx.post(
            f"{Config.BASE_URL}/core/conversations/users/{user_id}/bots/{Config.BOT_ID}/conversation",
            headers=headers,
            content=data,
            timeout=30  # Reduced timeout
        )
        response.raise_for_status()
        return response.json()['data']
    except httpx.RequestError as e:
        logger.error(f"Failed to create conversation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")

def clean_thinking_content(content: str) -> str:
    """Clean thinking process content"""
    if "```ys_think" in content:
        cleaned = re.sub(r'```ys_think.*?```', '', content, flags=re.DOTALL)
        if cleaned and cleaned.strip():
            return cleaned.strip()
        return ""
    return content

def remove_reference_annotations(content: str) -> str:
    """Enhanced removal of reference annotations"""
    # Remove various patterns of reference annotations
    patterns = [
        r'\[\d+\]$$@ref$$',  # [1](@ref)
        r'\[这是一个数字\]$$@ref$$',  # [这是一个数字](@ref)
        r'\[.*?\]$$@ref$$',  # Any text in brackets with (@ref)
        r'\[\d+\]',  # Simple [1] patterns
        r'@ref',  # Standalone @ref
    ]
    
    cleaned_content = content
    for pattern in patterns:
        cleaned_content = re.sub(pattern, '', cleaned_content)
    
    # Clean up extra spaces
    cleaned_content = re.sub(r'\s+', ' ', cleaned_content).strip()
    return cleaned_content

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
    """Process message event with enhanced cleaning"""
    content = data.get("content", "")
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))
    result = ""

    # Handle thinking blocks
    if "```ys_think" in content and not thinking_started:
        thinking_started = True
        in_thinking_block = True
        chunk = create_chunk(sse_id=sse_id, created=created, content="<Thinking>\n\n", is_first=is_first_chunk)
        result = f"data: \{json.dumps(chunk, ensure_ascii=False)\}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    if "```" in content and in_thinking_block:
        in_thinking_block = False
        chunk = create_chunk(sse_id=sse_id, created=created, content="\n</Thinking>\n\n")
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    if in_thinking_block:
        thinking_content.append(content)
        chunk = create_chunk(sse_id=sse_id, created=created, content=content)
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Clean content
    content = clean_thinking_content(content)
    if not content:
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Enhanced reference annotation removal
    content = remove_reference_annotations(content)
    if not content.strip():  # Skip if content becomes empty after cleaning
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    chunk = create_chunk(sse_id=sse_id, created=created, content=content, is_first=is_first_chunk)
    result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    return result, in_thinking_block, thinking_started, False, thinking_content

def process_generate_end_event(data: dict, in_thinking_block: bool, thinking_content: list) -> List[str]:
    """Process generation end event"""
    result = []
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))

    if in_thinking_block:
        end_thinking_chunk = create_chunk(sse_id=sse_id, created=created, content="\n<Thinking>
</Thinking>\n\n")
        result.append(f"data: {json.dumps(end_thinking_chunk, ensure_ascii=False)}\n\n")

    meta_chunk = create_chunk(sse_id=sse_id, created=created, 
                             meta={"thinking_content": "".join(thinking_content) if thinking_content else None})
    result.append(f"data: {json.dumps(meta_chunk, ensure_ascii=False)}\n\n")

    end_chunk = create_chunk(sse_id=sse_id, created=created, finish_reason="stop")
    result.append(f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n")
    result.append("data: [DONE]\n\n")
    return result

async def generate_response(messages: List[dict], model: str, temperature: float, stream: bool,
                            max_tokens: Optional[int] = None, presence_penalty: float = 0,
                            frequency_penalty: float = 0, top_p: float = 1.0) -> AsyncGenerator[str, None]:
    """Enhanced response generation with context support"""
    # Ensure session is initialized
    await session_manager.refresh_if_needed()
    
    # Get messages with context
    context_messages = session_manager.get_context_messages(messages)
    current_query = messages[-1]['content']
    
    # Add current user message to history
    session_manager.add_to_history("user", current_query)

    timestamp = generate_timestamp()
    payload = {
        'userId': session_manager.user_id,
        'botId': Config.BOT_ID,
        'botAlias': 'custom',
        'query': current_query,
        'isRetry': False,
        'breakingStrategy': 0,
        'isNewConversation': len(session_manager.conversation_history) <= 1,  # Dynamic conversation state
        'mediaInfos': [],
        'turnIndex': len(session_manager.conversation_history) - 1,
        'rewriteQuery': '',
        'conversationId': session_manager.conversation_id,
        'capabilities': [{
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
        }],
        'attachmentInfo': {'url': {'infoList': []}},
        'inputWay': 'proactive',
        'pureQuery': '',
    }
    
    data = json.dumps(payload, separators=(',', ':'))
    digest = calculate_sha256(data)
    headers = create_common_headers(timestamp, digest, session_manager.token, session_manager.device_id)
    headers.update({
        'accept': 'text/event-stream, text/event-stream',
        'x-yuanshi-appversioncode': '',
        'x-yuanshi-appversionname': '3.1.0',
    })

    assistant_response = ""
    
    try:
        # Use reduced timeout for faster response
        async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
            async with client.stream('POST', f"{Config.BASE_URL}/core/conversation/chat/v1",
                                     headers=headers, content=data) as response:
                response.raise_for_status()

                is_first_chunk = True
                current_event = None
                in_thinking_block = False
                thinking_content = []
                thinking_started = False

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        current_event = None
                        continue

                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                        continue

                    elif line.startswith("data:"):
                        json_str = line[len("data:"):].strip()
                        try:
                            data = json.loads(json_str)

                            if current_event == "message":
                                result, in_thinking_block, thinking_started, is_first_chunk, thinking_content = await process_message_event(
                                    data, is_first_chunk, in_thinking_block, thinking_started, thinking_content
                                )
                                if result:
                                    # Collect assistant response for history
                                    if not in_thinking_block and "content" in data:
                                        content = remove_reference_annotations(clean_thinking_content(data.get("content", "")))
                                        if content:
                                            assistant_response += content
                                    yield result

                            elif current_event == "generateEnd":
                                # Add assistant response to history
                                if assistant_response.strip():
                                    session_manager.add_to_history("assistant", assistant_response.strip())
                                
                                for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                    yield chunk

                        except json.JSONDecodeError as e:
                            logger.error(f"JSON parse error: {e}")
                            continue

    except httpx.RequestError as e:
        logger.error(f"Generate response error: {e}")
        try:
            session_manager.initialize()
            logger.info("Session reinitialized")
        except Exception as re_init_error:
            logger.error(f"Failed to reinitialize session: {re_init_error}")
        raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")

@app.get("/")
async def home():
    return {"status": "ok", "message": "Enhanced AI Chat Proxy Server is running!"}

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
async def chat_completions(request: ChatCompletionRequest, authorization: str = Header(None)):
    """Enhanced chat completion with context support"""
    await verify_api_key(authorization)
    
    logger.info(f"Chat request: model={request.model}, stream={request.stream}, messages={len(request.messages)}")
    messages = [msg.model_dump() for msg in request.messages]

    if not request.stream:
        # Non-streaming response
        content = ""
        thinking_content = ""
        meta = None
        in_thinking = False

        async for chunk_str in generate_response(
                messages=messages, model=request.model, temperature=request.temperature,
                stream=True, max_tokens=request.max_tokens,
                presence_penalty=request.presence_penalty,
                frequency_penalty=request.frequency_penalty, top_p=request.top_p
        ):
            try:
                if chunk_str.startswith("data: ") and not chunk_str.startswith("data: [DONE]"):
                    chunk = json.loads(chunk_str[len("data: "):])
                    if "choices" in chunk and chunk["choices"]:
                        delta = chunk["choices"][0]["delta"]
                        if "content" in delta:
                            content_part = delta["content"]
                            if content_part == "<Thinking>\n\n":
                                in_thinking = True
                                continue
                            elif content_part == "\n</Thinking>\n\n":
                                in_thinking = False
                                continue
                            if in_thinking:
                                thinking_content += content_part
                            else:
                                content += content_part
                        if "meta" in delta:
                            meta = delta["meta"]
            except Exception as e:
                logger.error(f"Error processing non-streaming response: {e}")

        content = remove_reference_annotations(content)
        
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
            messages=messages, model=request.model, temperature=request.temperature,
            stream=request.stream, max_tokens=request.max_tokens,
            presence_penalty=request.presence_penalty,
            frequency_penalty=request.frequency_penalty, top_p=request.top_p
        ),
        media_type="text/event-stream"
    )

@app.get("/memory")
async def memory_status():
    """Enhanced memory status endpoint"""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':
            mem_used = usage.ru_maxrss / 1024 / 1024
        else:
            mem_used = usage.ru_maxrss / 1024
        
        total_mem = 0
        free_mem = 0
        if os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemTotal' in line:
                        total_mem = int(line.split()[1]) / 1024
                    elif 'MemAvailable' in line:
                        free_mem = int(line.split()[1]) / 1024
        
        percent = (mem_used / total_mem * 100) if total_mem > 0 else 0
        
        return {
            "process_memory_mb": round(mem_used, 2),
            "total_memory_mb": round(total_mem, 2) if total_mem > 0 else "unknown",
            "available_memory_mb": round(free_mem, 2) if free_mem > 0 else "unknown",
            "memory_percent": round(percent, 2) if percent > 0 else "unknown",
            "gc_objects": len(gc.get_objects()),
            "conversation_history_size": len(session_manager.conversation_history),
            "cache_size": len(session_manager._cache),
            "last_cleanup": memory_manager.last_cleanup,
            "monitoring_active": memory_manager.is_running
        }
    except ImportError:
        return {
            "gc_objects": len(gc.get_objects()),
            "conversation_history_size": len(session_manager.conversation_history),
            "note": "Limited memory information available"
        }

@app.post("/memory/cleanup")
async def trigger_cleanup():
    """Enhanced manual memory cleanup"""
    try:
        import resource
        usage_before = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':
            mem_before = usage_before.ru_maxrss / 1024 / 1024
        else:
            mem_before = usage_before.ru_maxrss / 1024
    except ImportError:
        mem_before = 0
    
    obj_count_before = len(gc.get_objects())
    history_before = len(session_manager.conversation_history)
    
    # Enhanced cleanup
    memory_manager.cleanup_memory()
    
    # Clean old conversation history
    if len(session_manager.conversation_history) > session_manager.max_history:
        session_manager.conversation_history = session_manager.conversation_history[-session_manager.max_history:]
    
    try:
        import resource
        usage_after = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':
            mem_after = usage_after.ru_maxrss / 1024 / 1024
        else:
            mem_after = usage_after.ru_maxrss / 1024
        mem_diff = mem_before - mem_after
    except ImportError:
        mem_after = 0
        mem_diff = 0
    
    obj_count_after = len(gc.get_objects())
    history_after = len(session_manager.conversation_history)
    
    return {
        "status": "success",
        "memory_before_mb": round(mem_before, 2) if mem_before > 0 else "unknown",
        "memory_after_mb": round(mem_after, 2) if mem_after > 0 else "unknown",
        "memory_difference_mb": round(mem_diff, 2) if mem_diff != 0 else "unknown",
        "objects_before": obj_count_before,
        "objects_after": obj_count_after,
        "objects_difference": obj_count_before - obj_count_after,
        "history_before": history_before,
        "history_after": history_after,
        "history_cleaned": history_before - history_after
    }

@app.get("/conversation/history")
async def get_conversation_history():
    """Get current conversation history"""
    return {
        "history": session_manager.conversation_history,
        "total_messages": len(session_manager.conversation_history),
        "max_history": session_manager.max_history
    }

@app.delete("/conversation/history")
async def clear_conversation_history():
    """Clear conversation history"""
    old_count = len(session_manager.conversation_history)
    session_manager.conversation_history.clear()
    return {
        "status": "success",
        "cleared_messages": old_count,
        "remaining_messages": len(session_manager.conversation_history)
    }

@app.on_event("startup")
async def startup_event():
    """Enhanced startup initialization"""
    try:
        session_manager.initialize()
        memory_manager.start_monitoring()
        logger.info("Enhanced application started with memory monitoring and context support")
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
        raise

@app.get("/health")
async def health_check():
    """Enhanced health check"""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':
            mem_used = usage.ru_maxrss / 1024 / 1024
        else:
            mem_used = usage.ru_maxrss / 1024
        
        total_mem = 0
        if os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemTotal' in line:
                        total_mem = int(line.split()[1]) / 1024
                        break
        
        percent = (mem_used / total_mem * 100) if total_mem > 0 else 0
        
        status = "ok"
        if percent > 90:
            status = "warning"
            memory_manager.cleanup_memory()
    except ImportError:
        status = "ok"
        percent = 0
        mem_used = 0
    
    return {
        "status": status,
        "session": "active" if session_manager.is_initialized() else "inactive",
        "memory": {
            "percent": round(percent, 2) if percent > 0 else "unknown",
            "used_mb": round(mem_used, 2) if mem_used > 0 else "unknown"
        },
        "features": {
            "context_support": True,
            "reference_cleaning": True,
            "auto_memory_cleanup": memory_manager.is_running,
            "conversation_history": len(session_manager.conversation_history)
        }
    }
