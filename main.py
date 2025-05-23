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

# Memory and cleanup management
class MemoryManager:
    def __init__(self, threshold_percent=75, check_interval=30, cleanup_interval=300):
        self.threshold_percent = threshold_percent
        self.check_interval = check_interval
        self.cleanup_interval = cleanup_interval  # 5 minutes
        self.is_running = False
        self.monitor_thread = None
        self.cleanup_thread = None
        self.last_cleanup = time.time()
        
    def start_monitoring(self):
        """Start memory monitoring and auto cleanup threads"""
        if not self.is_running:
            self.is_running = True
            self.monitor_thread = threading.Thread(target=self._monitor_memory, daemon=True)
            self.cleanup_thread = threading.Thread(target=self._auto_cleanup, daemon=True)
            self.monitor_thread.start()
            self.cleanup_thread.start()
            logger.info("Memory monitoring and auto cleanup started")
    
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
    
    def _auto_cleanup(self):
        """Automatic periodic cleanup"""
        while self.is_running:
            try:
                current_time = time.time()
                if current_time - self.last_cleanup > self.cleanup_interval:
                    logger.info("Performing scheduled cleanup")
                    self.cleanup_memory()
                    # Clean up old sessions
                    session_manager.cleanup_old_conversations()
                    self.last_cleanup = current_time
                    
            except Exception as e:
                logger.error(f"Error in auto cleanup: {e}")
                
            time.sleep(60)  # Check every minute
    
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
            return (mem_used / 500) * 100  # Fallback to 500MB assumption
            
        except ImportError:
            gc.collect()
            obj_count = len(gc.get_objects())
            return (obj_count / 1000000) * 100
    
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
        """Perform comprehensive memory cleanup"""
        collected = gc.collect(generation=2)
        logger.info(f"Garbage collection: collected {collected} objects")
        
        try:
            httpx._pools.POOLS.clear()
            logger.info("Cleared httpx connection pools")
        except:
            pass
        
        try:
            import ctypes
            ctypes.CDLL('libc.so.6').malloc_trim(0)
            logger.info("Called malloc_trim to release memory to OS")
        except:
            pass

# Initialize memory manager
memory_manager = MemoryManager(threshold_percent=75, check_interval=30, cleanup_interval=300)

# Configuration
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
        self.conversations = {}  # Store multiple conversations by user/session
        self.conversation_history = {}  # Store conversation history
        self.client = None  # Pre-initialized HTTP client
        
    def initialize(self):
        """Initialize session and pre-warm connections"""
        self.device_id = generate_device_id()
        self.token, self.user_id = get_auth_token(self.device_id)
        
        # Pre-initialize HTTP client for faster responses
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(150),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )
        
        logger.info(f"Session initialized: user_id={self.user_id}")

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id])

    async def refresh_if_needed(self):
        """Refresh session if needed"""
        if not self.is_initialized():
            self.initialize()

    def get_or_create_conversation(self, session_id: str = "default"):
        """Get existing conversation or create new one"""
        if session_id not in self.conversations:
            conversation_id = create_conversation(self.device_id, self.token, self.user_id)
            self.conversations[session_id] = conversation_id
            self.conversation_history[session_id] = []
            logger.info(f"Created new conversation: {conversation_id} for session: {session_id}")
        return self.conversations[session_id]
    
    def add_to_history(self, session_id: str, role: str, content: str):
        """Add message to conversation history"""
        if session_id not in self.conversation_history:
            self.conversation_history[session_id] = []
        
        self.conversation_history[session_id].append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        
        # Keep only last 20 messages to prevent memory bloat
        if len(self.conversation_history[session_id]) > 20:
            self.conversation_history[session_id] = self.conversation_history[session_id][-20:]
    
    def get_context_messages(self, session_id: str, max_messages: int = 10):
        """Get recent conversation context"""
        if session_id not in self.conversation_history:
            return []
        
        history = self.conversation_history[session_id]
        return history[-max_messages:] if len(history) > max_messages else history
    
    def cleanup_old_conversations(self):
        """Clean up old conversations to free memory"""
        current_time = time.time()
        sessions_to_remove = []
        
        for session_id, history in self.conversation_history.items():
            if history and current_time - history[-1]["timestamp"] > 3600:  # 1 hour old
                sessions_to_remove.append(session_id)
        
        for session_id in sessions_to_remove:
            if session_id in self.conversations:
                del self.conversations[session_id]
            if session_id in self.conversation_history:
                del self.conversation_history[session_id]
            logger.info(f"Cleaned up old conversation: {session_id}")

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
            timeout=300
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
            timeout=300
        )
        response.raise_for_status()
        return response.json()['data']
    except httpx.RequestError as e:
        logger.error(f"Failed to create conversation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse conversation response: {e}")
        raise HTTPException(status_code=500, detail="Server returned invalid conversation data")

def clean_ai_response(content: str) -> str:
    """Clean AI response by removing reference annotations and thinking blocks"""
    if not content:
        return content
    
    # Remove reference annotations like [1](@ref), [2](@ref), etc.
    content = re.sub(r'\[\d+\]$$@ref$$', '', content)
    
    # Remove thinking blocks
    content = re.sub(r'```ys_think.*?```', '', content, flags=re.DOTALL)
    
    # Clean up extra whitespace
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    content = content.strip()
    
    return content

def build_context_query(messages: List[dict], session_id: str) -> str:
    """Build query with context from conversation history"""
    # Get recent context
    context_messages = session_manager.get_context_messages(session_id, max_messages=6)
    
    # Build context string
    context_parts = []
    for msg in context_messages[-6:]:  # Last 6 messages for context
        role = "用户" if msg["role"] == "user" else "助手"
        context_parts.append(f"{role}: {msg['content'][:200]}")  # Limit length
    
    current_query = messages[-1]['content']
    
    if context_parts:
        context_str = "\n".join(context_parts)
        return f"对话历史:\n{context_str}\n\n当前问题: {current_query}"
    
    return current_query

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
    """Process message event with improved reference cleaning"""
    content = data.get("content", "")
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))
    result = ""

    # Check for thinking block start
    if "```ys_think" in content and not thinking_started:
        thinking_started = True
        in_thinking_block = True
        chunk = create_chunk(sse_id=sse_id, created=created, content="<Thinking>\n\n", is_first=is_first_chunk)
        result = f"data: \{json.dumps(chunk, ensure_ascii=False)\}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Check for thinking block end
    if "```" in content and in_thinking_block:
        in_thinking_block = False
        chunk = create_chunk(sse_id=sse_id, created=created, content="\n</Thinking>\n\n")
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Handle thinking block content
    if in_thinking_block:
        thinking_content.append(content)
        chunk = create_chunk(sse_id=sse_id, created=created, content=content)
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Clean normal content
    content = clean_ai_response(content)
    if not content:
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
                            session_id: str = "default", max_tokens: Optional[int] = None, 
                            presence_penalty: float = 0, frequency_penalty: float = 0, 
                            top_p: float = 1.0) -> AsyncGenerator[str, None]:
    """Generate response with context support and faster first token"""
    await session_manager.refresh_if_needed()
    
    # Get or create conversation for this session
    conversation_id = session_manager.get_or_create_conversation(session_id)
    
    # Build query with context
    query = build_context_query(messages, session_id)
    
    # Add current user message to history
    session_manager.add_to_history(session_id, "user", messages[-1]['content'])

    timestamp = generate_timestamp()
    payload = {
        'userId': session_manager.user_id,
        'botId': Config.BOT_ID,
        'botAlias': 'custom',
        'query': query,
        'isRetry': False,
        'breakingStrategy': 0,
        'isNewConversation': False,  # Use existing conversation for context
        'mediaInfos': [],
        'turnIndex': len(session_manager.get_context_messages(session_id)),
        'rewriteQuery': '',
        'conversationId': conversation_id,
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
        # Use pre-initialized client for faster response
        client = session_manager.client or httpx.AsyncClient(timeout=httpx.Timeout(150))
        
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
                                if not in_thinking_block:
                                    content = data.get("content", "")
                                    assistant_response += clean_ai_response(content)
                                yield result

                        elif current_event == "generateEnd":
                            for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                yield chunk
                            # Add assistant response to history
                            if assistant_response.strip():
                                session_manager.add_to_history(session_id, "assistant", assistant_response.strip())

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
async def root():
    return {"status": "ok", "message": "AI Proxy API is running with context support and memory optimization"}

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
    """Process chat completion requests with context support"""
    await verify_api_key(authorization)
    
    # Extract session ID from user field or generate one
    session_id = request.user or "default"
    
    logger.info(f"Chat request: model={request.model}, stream={request.stream}, session={session_id}")
    messages = [msg.model_dump() for msg in request.messages]

    if not request.stream:
        # Non-streaming response
        content = ""
        thinking_content = ""
        meta = None
        in_thinking = False

        async for chunk_str in generate_response(
                messages=messages,
                model=request.model,
                temperature=request.temperature,
                stream=True,
                session_id=session_id,
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
            session_id=session_id,
            max_tokens=request.max_tokens,
            presence_penalty=request.presence_penalty,
            frequency_penalty=request.frequency_penalty,
            top_p=request.top_p
        ),
        media_type="text/event-stream"
    )

@app.get("/memory")
async def memory_status():
    """Get memory and session status"""
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
        
        return {
            "memory": {
                "process_memory_mb": round(mem_used, 2),
                "total_memory_mb": round(total_mem, 2) if total_mem > 0 else "unknown",
                "memory_percent": round(percent, 2) if percent > 0 else "unknown",
                "gc_objects": len(gc.get_objects())
            },
            "sessions": {
                "active_conversations": len(session_manager.conversations),
                "total_history_messages": sum(len(h) for h in session_manager.conversation_history.values())
            }
        }
    except ImportError:
        return {
            "memory": {"gc_objects": len(gc.get_objects()), "note": "Limited info available"},
            "sessions": {
                "active_conversations": len(session_manager.conversations),
                "total_history_messages": sum(len(h) for h in session_manager.conversation_history.values())
            }
        }

@app.post("/memory/cleanup")
async def trigger_cleanup():
    """Manually trigger comprehensive cleanup"""
    obj_count_before = len(gc.get_objects())
    conversations_before = len(session_manager.conversations)
    
    memory_manager.cleanup_memory()
    session_manager.cleanup_old_conversations()
    
    obj_count_after = len(gc.get_objects())
    conversations_after = len(session_manager.conversations)
    
    return {
        "status": "success",
        "objects_cleaned": obj_count_before - obj_count_after,
        "conversations_cleaned": conversations_before - conversations_after,
        "remaining_conversations": conversations_after
    }

@app.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clear specific session history"""
    if session_id in session_manager.conversations:
        del session_manager.conversations[session_id]
    if session_id in session_manager.conversation_history:
        del session_manager.conversation_history[session_id]
    return {"status": "success", "message": f"Session {session_id} cleared"}

@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    try:
        session_manager.initialize()
        memory_manager.start_monitoring()
        logger.info("Application started with context support and auto cleanup")
    except Exception as e:
        logger.error(f"Startup error: {e}")
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
        "memory_percent": round(percent, 2) if percent > 0 else "unknown",
        "active_conversations": len(session_manager.conversations),
        "features": ["context_support", "auto_cleanup", "reference_removal", "fast_response"]
    }
