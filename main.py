import asyncio
import json
import random
import uuid
import datetime
import time
import re
import gc
import os
import psutil
from fastapi import FastAPI, HTTPException, Header, Request, BackgroundTasks
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

from fastapi.middleware.cors import CORSMiddleware  # 添加到顶部导入部分

# 在创建FastAPI实例后添加
app = FastAPI()

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境应更严格
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

# 全局变量，用于跟踪最后一次API调用的时间
last_api_call_time = time.time()
# 内存使用阈值（百分比），超过此值将触发垃圾回收
MEMORY_THRESHOLD = 70
# 垃圾回收间隔（秒）
GC_INTERVAL = 300  # 5分钟

# 添加配置类来管理API配置
class Config:
    API_KEY = "TkoWuEN8cpDJubb7Zfwxln16NQDZIc8z"
    BASE_URL = "https://api-bj.wenxiaobai.com/api/v1.0"
    BOT_ID = 200006
    DEFAULT_MODEL = "DeepSeek-R1"


# 添加会话管理类
class SessionManager:
    def __init__(self):
        self.device_id = None
        self.token = None
        self.user_id = None
        self.conversation_id = None

    def initialize(self):
        """初始化会话"""
        self.device_id = generate_device_id()
        self.token, self.user_id = get_auth_token(self.device_id)
        self.conversation_id = create_conversation(self.device_id, self.token, self.user_id)
        logger.info(f"Session initialized: user_id={self.user_id}, conversation_id={self.conversation_id}")

    def is_initialized(self):
        """检查会话是否已初始化"""
        return all([self.device_id, self.token, self.user_id, self.conversation_id])

    async def refresh_if_needed(self):
        """如果需要，刷新会话"""
        if not self.is_initialized():
            self.initialize()


# 创建会话管理器实例
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
    """生成设备ID"""
    return f"{uuid.uuid4().hex}_{int(time.time() * 1000)}_{random.randint(100000, 999999)}"


def generate_timestamp() -> str:
    """生成符合要求的UTC时间字符串"""
    timestamp_ms = int(time.time() * 1000) + 559
    utc_time = datetime.datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    return utc_time.strftime('%a, %d %b %Y %H:%M:%S GMT')


def calculate_sha256(data: str) -> str:
    """计算SHA-256摘要"""
    sha256 = hashlib.sha256(data.encode()).digest()
    return base64.b64encode(sha256).decode()


def generate_signature(timestamp: str, digest: str) -> str:
    """生成请求签名"""
    message = f"x-date: {timestamp}\ndigest: SHA-256={digest}"
    signature = hmac.new(
        Config.API_KEY.encode(),
        message.encode(),
        hashlib.sha1
    ).digest()
    return base64.b64encode(signature).decode()


def create_common_headers(timestamp: str, digest: str, token: Optional[str] = None,
                          device_id: Optional[str] = None) -> dict:
    """创建通用请求头"""
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
    """获取认证令牌"""
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
        logger.error(f"获取认证令牌失败: {e}")
        raise HTTPException(status_code=500, detail=f"认证失败: {str(e)}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"解析认证响应失败: {e}")
        raise HTTPException(status_code=500, detail="服务器返回了无效的认证数据")


def create_conversation(device_id: str, token: str, user_id: str) -> str:
    """创建新的会话"""
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
        logger.error(f"创建会话失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"解析会话响应失败: {e}")
        raise HTTPException(status_code=500, detail="服务器返回了无效的会话数据")


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


# 辅助函数：验证 API 密钥
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
    """创建响应块"""
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
    """处理消息事件"""
    content = data.get("content", "")
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))
    result = ""

    # 检查是否是思考块的开始
    if "```ys_think" in content and not thinking_started:
        thinking_started = True
        in_thinking_block = True
        # 发送思考块开始标记
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="<Thinking>\n\n",
            is_first=is_first_chunk
        )
        result = f"data: \{json.dumps(chunk, ensure_ascii=False)\}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # 检查是否是思考块的结束
    if "```" in content and in_thinking_block:
        in_thinking_block = False
        # 发送思考块结束标记
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="\n</Thinking>\n\n"
        )
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # 如果在思考块内，收集思考内容
    if in_thinking_block:
        thinking_content.append(content)
        # 在思考块内也发送内容，但标记为思考内容
        chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content=content
        )
        result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # 清理内容，移除思考块
    content = clean_thinking_content(content)
    if not content:  # 如果清理后内容为空，跳过
        return result, in_thinking_block, thinking_started, is_first_chunk, thinking_content

    # 清理引用标记 [数字](@ref) 格式
    content = clean_reference_annotations(content)

    # 正常发送内容
    chunk = create_chunk(
        sse_id=sse_id,
        created=created,
        content=content,
        is_first=is_first_chunk
    )
    result = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    return result, in_thinking_block, thinking_started, False, thinking_content


def process_generate_end_event(data: dict, in_thinking_block: bool, thinking_content: list) -> List[str]:
    """处理生成结束事件"""
    result = []
    timestamp = data.get("timestamp", "")
    created = int(timestamp) // 1000 if timestamp else int(time.time())
    sse_id = data.get('sseId', str(uuid.uuid4()))

    # 如果思考块还没有结束，发送结束标记
    if in_thinking_block:
        end_thinking_chunk = create_chunk(
            sse_id=sse_id,
            created=created,
            content="\n<Thinking>
</Thinking>\n\n"
        )
        result.append(f"data: {json.dumps(end_thinking_chunk, ensure_ascii=False)}\n\n")

    # 添加元数据
    meta_chunk = create_chunk(
        sse_id=sse_id,
        created=created,
        meta={"thinking_content": "".join(thinking_content) if thinking_content else None}
    )
    result.append(f"data: {json.dumps(meta_chunk, ensure_ascii=False)}\n\n")

    # 发送结束标记
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
    """生成响应 - 使用真正的流式处理"""
    # 确保会话已初始化
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

    # 创建流式请求的特殊头部
    headers = create_common_headers(timestamp, digest, session_manager.token, session_manager.device_id)
    headers.update({
        'accept': 'text/event-stream, text/event-stream',
        'x-yuanshi-appversioncode': '',
        'x-yuanshi-appversionname': '3.1.0',
    })

    try:
        # 使用 stream=True 参数，实现真正的流式处理
        async with httpx.AsyncClient(timeout=httpx.Timeout(900)) as client:
            async with client.stream('POST', f"{Config.BASE_URL}/core/conversation/chat/v1",
                                     headers=headers, content=data) as response:
                response.raise_for_status()

                # 处理流式响应
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

                    # 解析事件类型
                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                        continue

                    # 处理数据行
                    elif line.startswith("data:"):
                        json_str = line[len("data:"):].strip()
                        try:
                            data = json.loads(json_str)

                            # 处理消息事件
                            if current_event == "message":
                                result, in_thinking_block, thinking_started, is_first_chunk, thinking_content = await process_message_event(
                                    data, is_first_chunk, in_thinking_block, thinking_started, thinking_content
                                )
                                if result:
                                    yield result

                            # 处理生成结束事件
                            elif current_event == "generateEnd":
                                for chunk in process_generate_end_event(data, in_thinking_block, thinking_content):
                                    yield chunk

                        except json.JSONDecodeError as e:
                            logger.error(f"JSON解析错误: {e}")
                            continue

    except httpx.RequestError as e:
        logger.error(f"生成响应错误: {e}")
        # 尝试重新初始化会话
        try:
            session_manager.initialize()
            logger.info("会话已重新初始化")
        except Exception as re_init_error:
            logger.error(f"重新初始化会话失败: {re_init_error}")
        raise HTTPException(status_code=500, detail=f"请求错误: {str(e)}")


# 新增函数：清理引用标记 [数字](@ref) 格式
def clean_reference_annotations(content: str) -> str:
    """清理引用标记，如 [这是一个数字](@ref)"""
    # 使用正则表达式移除 [任何内容](@ref) 格式的文本
    cleaned = re.sub(r'\[[^\]]*\]$$@ref$$', '', content)
    return cleaned


# 新增函数：获取当前内存使用情况
def get_memory_usage() -> float:
    """获取当前内存使用百分比"""
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    memory_percent = process.memory_percent()
    logger.info(f"内存使用: {memory_info.rss / 1024 / 1024:.2f} MB ({memory_percent:.2f}%)")
    return memory_percent


# 新增函数：执行垃圾回收
def perform_garbage_collection():
    """执行垃圾回收并记录结果"""
    before_memory = get_memory_usage()
    
    # 执行完整的垃圾回收
    collected = gc.collect(2)  # 强制收集所有代
    
    after_memory = get_memory_usage()
    memory_freed = before_memory - after_memory
    
    logger.info(f"垃圾回收完成: 收集了 {collected} 个对象, 释放了 {memory_freed:.2f}% 内存")
    return collected, memory_freed


# 新增函数：检查是否需要执行垃圾回收
async def check_and_perform_gc():
    """检查是否需要执行垃圾回收"""
    global last_api_call_time
    
    current_time = time.time()
    time_since_last_call = current_time - last_api_call_time
    
    # 如果距离上次API调用超过了GC_INTERVAL，并且内存使用超过阈值，则执行垃圾回收
    if time_since_last_call > GC_INTERVAL:
        memory_usage = get_memory_usage()
        if memory_usage > MEMORY_THRESHOLD:
            logger.info(f"执行计划垃圾回收: 距上次API调用 {time_since_last_call:.2f}秒, 内存使用 {memory_usage:.2f}%")
            perform_garbage_collection()
        else:
            logger.info(f"跳过垃圾回收: 内存使用 {memory_usage:.2f}% 低于阈值 {MEMORY_THRESHOLD}%")


# 新增函数：更新最后API调用时间
def update_last_api_call_time():
    """更新最后API调用时间"""
    global last_api_call_time
    last_api_call_time = time.time()


# 新增函数：后台垃圾回收任务
async def background_gc_task():
    """后台垃圾回收任务"""
    while True:
        await check_and_perform_gc()
        await asyncio.sleep(GC_INTERVAL)  # 每隔GC_INTERVAL秒检查一次


@app.get("/")
async def hff(background_tasks: BackgroundTasks):
    update_last_api_call_time()
    background_tasks.add_task(check_and_perform_gc)
    return {"status": "ok", "提示": "hefengfan接口已成功部署！"}


@app.get("/v1/models")
async def list_models(background_tasks: BackgroundTasks):
    """列出可用模型"""
    update_last_api_call_time()
    background_tasks.add_task(check_and_perform_gc)
    
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
    """处理聊天完成请求"""
    # 更新最后API调用时间
    update_last_api_call_time()
    
    # 验证 API 密钥
    await verify_api_key(authorization)

    # 添加请求日志
    logger.info(f"Received chat request: model={request.model}, stream={request.stream}")
    messages = [msg.model_dump() for msg in request.messages]

    if not request.stream:
        # 非流式响应处理
        content = ""
        thinking_content = ""
        meta = None
        in_thinking = False

        async for chunk_str in generate_response(
                messages=messages,
                model=request.model,
                temperature=request.temperature,
                stream=True,  # 内部仍使用流式处理
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

                            # 处理思考块标记
                            if content_part == "<Thinking>\n\n":
                                in_thinking = True
                                continue
                            elif content_part == "\n</Thinking>\n\n":
                                in_thinking = False
                                continue

                            # 收集内容
                            if in_thinking:
                                thinking_content += content_part
                            else:
                                content += content_part

                        # 收集元数据
                        if "meta" in delta:
                            meta = delta["meta"]
            except Exception as e:
                logger.error(f"处理非流式响应错误: {e}")

        # 清理引用标记
        content = clean_reference_annotations(content)
        
        # 构建完整响应
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

    # 流式响应
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
    """应用启动时初始化会话"""
    try:
        session_manager.initialize()
        # 启动后台垃圾回收任务
        asyncio.create_task(background_gc_task())
        logger.info("后台垃圾回收任务已启动")
    except Exception as e:
        logger.error(f"启动初始化错误: {e}")
        raise


@app.get("/health")
async def health_check(background_tasks: BackgroundTasks):
    """健康检查端点"""
    update_last_api_call_time()
    background_tasks.add_task(check_and_perform_gc)
    
    memory_usage = get_memory_usage()
    if session_manager.is_initialized():
        return {
            "status": "ok", 
            "session": "active",
            "memory_usage": f"{memory_usage:.2f}%",
            "last_api_call": datetime.datetime.fromtimestamp(last_api_call_time).strftime('%Y-%m-%d %H:%M:%S')
        }
    else:
        return {
            "status": "degraded", 
            "session": "inactive",
            "memory_usage": f"{memory_usage:.2f}%",
            "last_api_call": datetime.datetime.fromtimestamp(last_api_call_time).strftime('%Y-%m-%d %H:%M:%S')
        }


# 添加手动触发垃圾回收的端点
@app.post("/maintenance/gc")
async def trigger_garbage_collection():
    """手动触发垃圾回收"""
    collected, memory_freed = perform_garbage_collection()
    return {
        "status": "ok",
        "collected_objects": collected,
        "memory_freed_percent": f"{memory_freed:.2f}%",
        "current_memory_usage": f"{get_memory_usage():.2f}%"
    }
