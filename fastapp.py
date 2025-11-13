import re
import os
import json
from datetime import datetime, timedelta

import pytz
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from ics import Calendar, Event
from ics.alarm import DisplayAlarm


# ----------------------------------------------------------------------
# 安装说明
# ----------------------------------------------------------------------
# pip install fastapi uvicorn openai ics pytz
# uvicorn fastapp:app --reload --host 0.0.0.0


# ----------------------------------------------------------------------
# FastAPI 初始化
# ----------------------------------------------------------------------
app = FastAPI()

# 允许跨域（开发阶段放开，生产环境请指定域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# DeepSeek API 初始化
# ----------------------------------------------------------------------
client = OpenAI(
    api_key="sk-1d2a4e3488be49b49ce3e47d82a50093",
    base_url="https://api.deepseek.com",
)

# 时区设置
tz = pytz.timezone("Asia/Shanghai")

# ics 文件路径
ICS_FILE = r"/home/fileManager/uploads/calendar.ics"


# ----------------------------------------------------------------------
# 初始化空日历文件
# ----------------------------------------------------------------------
if not os.path.exists(ICS_FILE):
    calendar = Calendar()
    calendar.creator = "Calendar App"
    lines = [line.rstrip()
             for line in calendar.serialize_iter() if line.strip()]
    with open(ICS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ----------------------------------------------------------------------
# Pydantic 数据模型
# ----------------------------------------------------------------------
class Notification(BaseModel):
    notification: str


class DeleteEvent(BaseModel):
    title: str
    start: str  # ISO8601 格式的开始时间


# ----------------------------------------------------------------------
# 添加事件接口
# ----------------------------------------------------------------------
@app.post("/add_event")
async def add_event(data: Notification):
    notification = data.notification
    timenow = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    weekday = datetime.now(tz).strftime("%A")

    prompt = f"""
        当前时间: {timenow} {weekday}
        请从下面的通知中提取任务的标题、时间、主要任务，并按照如下格式返回：
        {{
            "title": "事件标题",
            "start": "以下通知中任务开始时间(ISO8601格式, 例如2025-10-03T09:00:00)",
            "end": "以下通知中任务结束时间或者截至时间(ISO8601格式, 如果没写就自动+1小时)",
            "location": "地点",
            "description": "总结任务内容并分条列出任务的主要内容"
        }}
        通知内容: {notification}
        """

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是一个日历助手"},
            {"role": "user", "content": prompt},
        ],
        stream=False,
    )

    # 提取模型返回的原始内容
    content = response.choices[0].message.content
    print(content)  # 调试：打印原始输出

    # 使用正则提取 JSON 部分
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        event_json = match.group(1)
    else:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        event_json = match.group(0) if match else content

    print(event_json)  # 调试：打印提取后的 JSON

    # 解析 JSON
    try:
        event_data = json.loads(event_json)
    except Exception as e:
        return {"error": "解析大模型输出失败", "raw": event_json, "exception": str(e)}

    # 读取现有日历
    with open(ICS_FILE, "r", encoding="utf-8") as f:
        calendar = Calendar(f.read())

    # 创建新事件
    event = Event()
    event.name = event_data.get("title", "未命名事件")

    # 处理开始时间
    start_str = event_data.get("start")
    if start_str:
        try:
            start_dt = datetime.fromisoformat(start_str)
            if start_dt.tzinfo is None:
                start_dt = tz.localize(start_dt)
            else:
                start_dt = start_dt.astimezone(tz)
            event.begin = start_dt
        except ValueError:
            return {"error": "无效的开始时间格式", "value": start_str}

    # 处理结束时间
    end_str = event_data.get("end")
    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str)
            if end_dt.tzinfo is None:
                end_dt = tz.localize(end_dt)
            else:
                end_dt = end_dt.astimezone(tz)
            event.end = end_dt
        except ValueError:
            return {"error": "无效的结束时间格式", "value": end_str}
    elif event.begin:
        event.end = event.begin + timedelta(hours=1)

    # 其他字段
    event.location = event_data.get("location", "")
    event.description = event_data.get("description", "")

    # 设置提醒：提前 1 小时、1 天、1 周
    if event.begin:
        alarms = [
            DisplayAlarm(trigger=timedelta(hours=-1)),
            DisplayAlarm(trigger=timedelta(hours=-24)),
            DisplayAlarm(trigger=timedelta(hours=-168)),
        ]
        for alarm in alarms:
            event.alarms.append(alarm)

    # 添加事件并保存
    calendar.events.add(event)
    lines = [line.rstrip()
             for line in calendar.serialize_iter() if line.strip()]
    with open(ICS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {"message": "事件已添加", "event": event_data}


# ----------------------------------------------------------------------
# 删除事件接口
# ----------------------------------------------------------------------
@app.post("/delete_event")
async def delete_event(data: DeleteEvent):
    title = data.title
    start_str = data.start

    # 解析开始时间
    try:
        start_dt = datetime.fromisoformat(start_str)
        if start_dt.tzinfo is None:
            start_dt = tz.localize(start_dt)
        else:
            start_dt = start_dt.astimezone(tz)
    except ValueError:
        return {"error": "无效的开始时间格式", "value": start_str}

    # 读取日历
    with open(ICS_FILE, "r", encoding="utf-8") as f:
        calendar = Calendar(f.read())

    # 查找并删除匹配的事件
    events_to_remove = [
        e
        for e in calendar.events
        if e.name == title and e.begin and e.begin == start_dt
    ]

    if not events_to_remove:
        return {"error": "未找到匹配的事件"}

    for e in events_to_remove:
        calendar.events.remove(e)

    # 保存更新后的日历
    lines = [line.rstrip()
             for line in calendar.serialize_iter() if line.strip()]
    with open(ICS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {"message": f"已删除 {len(events_to_remove)} 个事件"}


# ----------------------------------------------------------------------
# 查询未来事件接口
# ----------------------------------------------------------------------
@app.get("/get_events")
async def get_events():
    now = datetime.now(tz)

    with open(ICS_FILE, "r", encoding="utf-8") as f:
        calendar = Calendar(f.read())

    upcoming = []
    for event in calendar.events:
        if event.end and event.end > now:
            upcoming.append(
                {
                    "title": event.name,
                    "start": event.begin.isoformat(),
                    "end": event.end.isoformat() if event.end else None,
                    "location": event.location,
                    "description": event.description,
                }
            )

    # 按剩余时间排序
    def remaining_time(item):
        start = datetime.fromisoformat(item["start"])
        end = datetime.fromisoformat(item["end"]) if item["end"] else None
        if start <= now:
            return end - now if end else timedelta.max
        return start - now

    upcoming.sort(key=remaining_time)

    return {"events": upcoming}


# ----------------------------------------------------------------------
# 首页（返回 index.html）
# ----------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse("index.html")
