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

# Memory management configuration
class MemoryManager:
    def __init__(self, threshold_percent=80, check_interval=60):
        self.threshold_percent = threshold_percent  # Memory usage threshold to trigger cleanup
        self.check_interval = check_interval  # Check interval in seconds
        self.is_running = False
        self.monitor_thread = None
        
    def start_monitoring(self):
        """Start the memory monitoring thread"""
        if not self.is_running:
            self.is_running = True
            self.monitor_thread = threading.Thread(target=self._monitor_memory, daemon=True)
            self.monitor_thread.start()
            logger.info("Memory monitoring started")
    
    def _monitor_memory(self):
        """Monitor memory usage and trigger cleanup when necessary"""
        while self.is_running:
            try:
                memory_percent = self.get_memory_usage()
                
                logger.info(f"Current memory usage: {memory_percent:.1f}%")
                
                if memory_percent > self.threshold_percent:
                    logger.warning(f"Memory usage high ({memory_percent:.1f}%), triggering cleanup")
                    self.cleanup_memory()
                    
            except Exception as e:
                logger.error(f"Error in memory monitoring: {e}")
                
            time.sleep(self.check_interval)
    
    def get_memory_usage(self):
        """Get current memory usage percentage using built-in methods"""
        try:
            # Try to use resource module (Unix-like systems)
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # Convert to MB (ru_maxrss is in KB on Linux, bytes on macOS)
            if sys.platform == 'darwin':  # macOS
                mem_used = usage.ru_maxrss / 1024 / 1024
            else:  # Linux
                mem_used = usage.ru_maxrss / 1024
                
            # Get total memory from /proc/meminfo on Linux
            total_mem = self._get_total_memory()
            if total_mem > 0:
                return (mem_used / total_mem) * 100
            
            # Fallback: estimate based on process size relative to expected total
            # Assuming the server has 500MB as mentioned
            return (mem_used / 500) * 100
            
        except ImportError:
            # Fallback for systems without resource module
            # Use a simple heuristic based on available objects
            gc.collect()  # Collect first to get accurate count
            obj_count = len(gc.get_objects())
            # Rough estimate - adjust threshold based on testing
            threshold = 1000000  # Arbitrary large number of objects
            return (obj_count / threshold) * 100
    
    def _get_total_memory(self):
        """Get total system memory in MB"""
        try:
            # Try to read from /proc/meminfo on Linux
            if os.path.exists('/proc/meminfo'):
                with open('/proc/meminfo', 'r') as f:
                    for line in f:
                        if 'MemTotal' in line:
                            # Extract value in KB and convert to MB
                            return int(line.split()[1]) / 1024
            return 0
        except:
            return 0
    
    def cleanup_memory(self):
        """Perform memory cleanup operations"""
        # Force garbage collection
        collected = gc.collect(generation=2)
        logger.info(f"Garbage collection: collected {collected} objects")
        
        # Clear httpx cache if possible
        try:
            httpx._pools.POOLS.clear()
            logger.info("Cleared httpx connection pools")
        except:
            pass
        
        # Additional cleanup for Python memory fragmentation
        try:
            import ctypes
            ctypes.CDLL('libc.so.6').malloc_trim(0)
            logger.info("Called malloc_trim to release memory to OS")
        except:
            pass
        
        # Log memory after cleanup
        memory_percent = self.get_memory_usage()
        logger.info(f"Memory after cleanup: {memory_percent:.1f}%")

# Initialize memory manager
memory_manager = MemoryManager(threshold_percent=75, check_interval=30)

# Add configuration class to manage API configuration
class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"


# Add session management class
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None

    def initialize(self):
        """Initialize session"""
        self.device_id = generate_device_id()
        self.token, self.user_id = get_auth_token(self.device_id)
        self.conversation_id = create_conversation(self.device_id, self.token, self.user_id)
        logger.info(f"Session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")

    def is_initialized(self):
        """Check if session is initialized"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    async def refresh_if_needed(self):
        """Refresh session if needed"""
        if not self.is_initialized():
            self.initialize()


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


def is_thinking_content(content: str) -> bool:
    """判断内容是否为思考过程"""
    return "```ys_think" in content


def clean_thinking_content(content: str) -> str:
    """清理思考过程内容，移除特殊标记"""
    # 移除整个思考块
    if "```ys_think" in content:
        # 使用正则表达式移除整个思考块
        cleaned = re.sub(r'```ys_think.*?```', '', content, flags=re.DOTALL)
        # 如果清理后只剩下空白字符，返回空字符串
        if cleaned and cleaned.strip():
            return cleaned.strip()
        return ""
    return content


# Remove reference annotations like [1](@ref) from content
def remove_reference_annotations(content: str) -> str:
    """Remove reference annotations like [1](@ref) from content"""
    # Pattern to match [number](@ref) or similar reference annotations
    pattern = r'$$\d+$$$@ref$'
    cleaned_content = re.sub(pattern, '', content)
    return cleaned_content


# Helper function: Verify API key
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

    # Check if it's the start of a thinking block
    if "```ys_think" in content and not thinking_started:
        thinking_started = True
        in_thinking_block = True
        # 发送思考块开始标记
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="<think>\n\n",
            is_first=is_first_chunk
        )
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # Check if it's the end of a thinking block
    if "```" in content and in_thinking_block:
        in_thinking_block = False
        # 发送思考块结束标记
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="\n</think>\n\n"
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

    # Remove reference annotations
    content = remove_reference_annotations(content)
                                    
    # Clean content, remove thinking block
    content = clean_thinking_content(content)
    if not content:  # If content is empty after cleaning, skip
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
            content="\n</Thinking>\n\n"
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
    """Generate response - using true streaming"""
    # Ensure session is initialized
    await session_manager.refresh_if_needed()

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
                'botId': 210035,
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

    try:
        # Use stream=True parameter for true streaming
        async with httpx.AsyncClient(timeout=httpx.Timeout(150)) as client:
            async with client.stream('POST', f"{Config.BASE_URL}/core/conversation/chat/v1",
                                     headers=headers, content=data) as response:
                response.raise_for_status()

                # Process streaming response
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
                            logger.error(f"JSON parse error: {e}")
                            continue

    except httpx.RequestError as e:
        logger.error(f"Generate response error: {e}")
        # Try to reinitialize session
        try:
            session_manager.initialize()
            logger.info("Session reinitialized")
        except Exception as re_init_error:
            logger.error(f"Failed to reinitialize session: {re_init_error}")
        raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")

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
async def chat_completions(request: ChatCompletionRequest, authorization: str = Header(None)):
    """Process chat completion requests"""
    # Verify API key
    await verify_api_key(authorization)

    # Add request log
    logger.info(f"Received chat request: model={request.model}, stream={request.stream}")
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

        # Remove reference annotations from final content
        content = remove_reference_annotations(content)

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
            top_p=request.top_p
        ),
        media_type="text/event-stream"
    )


@app.get("/memory")
async def memory_status():
    """Get memory status"""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':  # macOS
            mem_used = usage.ru_maxrss / 1024 / 1024
        else:  # Linux
            mem_used = usage.ru_maxrss / 1024
        
        # Get total memory if possible
        total_mem = 0
        if os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemTotal' in line:
                        total_mem = int(line.split()[1]) / 1024
                        break
        
        # Get free memory if possible
        free_mem = 0
        if os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemAvailable' in line:
                        free_mem = int(line.split()[1]) / 1024
                        break
        
        # Calculate percentage if we have total memory
        percent = (mem_used / total_mem * 100) if total_mem > 0 else 0
        
        return {
            "process_memory_mb": round(mem_used, 2),
            "total_memory_mb": round(total_mem, 2) if total_mem > 0 else "unknown",
            "available_memory_mb": round(free_mem, 2) if free_mem > 0 else "unknown",
            "memory_percent": round(percent, 2) if percent > 0 else "unknown",
            "gc_objects": len(gc.get_objects())
        }
    except ImportError:
        # Fallback if resource module is not available
        return {
            "gc_objects": len(gc.get_objects()),
            "note": "Limited memory information available (resource module not found)"
        }


@app.post("/memory/cleanup")
async def trigger_cleanup():
    """Manually trigger memory cleanup"""
    # Get memory before cleanup
    try:
        import resource
        usage_before = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':  # macOS
            mem_before = usage_before.ru_maxrss / 1024 / 1024
        else:  # Linux
            mem_before = usage_before.ru_maxrss / 1024
    except ImportError:
        mem_before = 0
    
    # Count objects before cleanup
    obj_count_before = len(gc.get_objects())
    
    # Perform cleanup
    memory_manager.cleanup_memory()
    
    # Get memory after cleanup
    try:
        import resource
        usage_after = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':  # macOS
            mem_after = usage_after.ru_maxrss / 1024 / 1024
        else:  # Linux
            mem_after = usage_after.ru_maxrss / 1024
        
        mem_diff = mem_before - mem_after
    except ImportError:
        mem_after = 0
        mem_diff = 0
    
    # Count objects after cleanup
    obj_count_after = len(gc.get_objects())
    obj_diff = obj_count_before - obj_count_after
    
    return {
        "status": "success",
        "memory_before_mb": round(mem_before, 2) if mem_before > 0 else "unknown",
        "memory_after_mb": round(mem_after, 2) if mem_after > 0 else "unknown",
        "memory_difference_mb": round(mem_diff, 2) if mem_diff != 0 else "unknown",
        "objects_before": obj_count_before,
        "objects_after": obj_count_after,
        "objects_difference": obj_diff
    }


@app.on_event("startup")
async def startup_event():
    """Initialize session on application startup"""
    try:
        session_manager.initialize()
        # Start memory monitoring
        memory_manager.start_monitoring()
        logger.info("Application started with memory monitoring")
    except Exception as e:
        logger.error(f"Startup initialization error: {e}")
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Get memory usage
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':  # macOS
            mem_used = usage.ru_maxrss / 1024 / 1024
        else:  # Linux
            mem_used = usage.ru_maxrss / 1024
        
        # Get total memory if possible
        total_mem = 0
        if os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemTotal' in line:
                        total_mem = int(line.split()[1]) / 1024
                        break
        
        # Calculate percentage if we have total memory
        percent = (mem_used / total_mem * 100) if total_mem > 0 else 0
        
        # Determine status
        status = "ok"
        if percent > 90:
            status = "warning"
            # Trigger cleanup if memory usage is very high
            memory_manager.cleanup_memory()
    except ImportError:
        # Fallback if resource module is not available
        status = "ok"
        percent = 0
        mem_used = 0
    
    if session_manager.is_initialized():
        return {
            "status": status, 
            "session": "active",
            "memory": {
                "percent": round(percent, 2) if percent > 0 else "unknown",
                "used_mb": round(mem_used, 2) if mem_used > 0 else "unknown"
            }
        }
    else:
        return {"status": "degraded", "session": "inactive"}
