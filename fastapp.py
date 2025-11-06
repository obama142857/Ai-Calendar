import re
from ics import Calendar, Event

from ics.alarm import DisplayAlarm

import os

import json

from fastapi import FastAPI

from fastapi.responses import FileResponse

from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from openai import OpenAI

from datetime import datetime, timedelta

import pytz


# 安装

# pip install fastapi uvicorn openai ics

# uvicorn fastapp:app --reload --host


# ------------------------

# FastAPI 初始化

# ------------------------

app = FastAPI()


# 允许跨域，支持浏览器访问

app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],   # 开发阶段允许所有，生产环境可以指定域名

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)


# ------------------------

# DeepSeek API 初始化

# ------------------------

client = OpenAI(

    api_key="sk-1d2a4e3488be49b49ce3e47d82a50093",

    base_url="https://api.deepseek.com"

)


# 时区设置

tz = pytz.timezone('Asia/Shanghai')


ICS_FILE = r"/home/fileManager/uploads/calendar.ics"


# 初始化一个空日历文件

if not os.path.exists(ICS_FILE):

    calendar = Calendar()

    calendar.creator = "Calendar App"  # PRODID 会自动生成

    lines = [line.rstrip()

             for line in calendar.serialize_iter() if line.strip()]

    with open(ICS_FILE, "w", encoding="utf-8") as f:

        f.write('\n'.join(lines) + '\n')


# ------------------------

# Pydantic 数据模型

# ------------------------


class Notification(BaseModel):

    notification: str


class DeleteEvent(BaseModel):

    title: str

    start: str  # ISO8601格式的开始时间


# ------------------------

# 添加事件接口

# ------------------------


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

        messages=[{"role": "system", "content": "你是一个日历助手"},

                  {"role": "user", "content": prompt}],

        stream=False

    )

    # 假设这是你的原始内容（可能包含 ```json
    content = response.choices[0].message.content
    print(content)  # 可选：打印原始内容查看

    # 使用正则提取 JSON 部分
    match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
    if match:
        event_json = match.group(1)
    else:
        # 如果没有 markdown 包裹，直接尝试提取 { ... }
        match = re.search(r'\{.*\}', content, re.DOTALL)
        event_json = match.group(0) if match else content

    print(event_json)  # 打印提取后的纯 JSON

    try:

        event_data = json.loads(event_json)

    except Exception:

        return {"error": "解析大模型输出失败", "raw": event_json}

    # 读取已有日历（使用 UTF-8）

    with open(ICS_FILE, "r", encoding="utf-8") as f:

        calendar = Calendar(f.read())

    # 创建新事件

    event = Event()

    event.name = event_data.get("title", "未命名事件")

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

            return {"error": "无效的开始时间格式"}

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

            return {"error": "无效的结束时间格式"}

    elif event.begin:

        event.end = event.begin + timedelta(hours=1)

    event.location = event_data.get("location", "")

    event.description = event_data.get("description", "")

    # 统一设置提醒：事件开始前1小时、1天、1周

    if event.begin:

        alarms = [
            DisplayAlarm(trigger=timedelta(hours=-24)),

            DisplayAlarm(trigger=timedelta(hours=-1)),

            

            DisplayAlarm(trigger=timedelta(hours=-168))

        ]

        for alarm in alarms:

            event.alarms.append(alarm)

    # 添加到日历

    calendar.events.add(event)

    # 保存日历（使用 UTF-8，并去除空行）

    lines = [line.rstrip()

             for line in calendar.serialize_iter() if line.strip()]

    with open(ICS_FILE, "w", encoding="utf-8") as f:

        f.write('\n'.join(lines) + '\n')

    return {"message": "事件已添加", "event": event_data}


# ------------------------

# 删除事件接口

# ------------------------


@app.post("/delete_event")
async def delete_event(data: DeleteEvent):

    title = data.title

    start_str = data.start

    try:

        start_dt = datetime.fromisoformat(start_str)

        if start_dt.tzinfo is None:

            start_dt = tz.localize(start_dt)

        else:

            start_dt = start_dt.astimezone(tz)

    except ValueError:

        return {"error": "无效的开始时间格式"}

    # 读取已有日历（使用 UTF-8）

    with open(ICS_FILE, "r", encoding="utf-8") as f:

        calendar = Calendar(f.read())

    # 查找并删除匹配的事件

    events_to_remove = []

    for event in calendar.events:

        if (event.name == title and

            event.begin and

                event.begin == start_dt):

            events_to_remove.append(event)

    if not events_to_remove:

        return {"error": "未找到匹配的事件"}

    for event in events_to_remove:

        calendar.events.remove(event)

    # 保存日历（使用 UTF-8，并去除空行）

    lines = [line.rstrip()

             for line in calendar.serialize_iter() if line.strip()]

    with open(ICS_FILE, "w", encoding="utf-8") as f:

        f.write('\n'.join(lines) + '\n')

    return {"message": f"已删除 {len(events_to_remove)} 个事件"}


# ------------------------

# 查询事件接口

# ------------------------


@app.get("/get_events")
async def get_events():

    now = datetime.now(tz)

    with open(ICS_FILE, "r", encoding="utf-8") as f:

        calendar = Calendar(f.read())

    upcoming = []

    for event in calendar.events:

        if event.end and event.end > now:

            upcoming.append({

                "title": event.name,

                "start": event.begin.isoformat(),

                "end": event.end.isoformat() if event.end else None,

                "location": event.location,

                "description": event.description

            })

    def remaining(x):

        start = datetime.fromisoformat(x["start"])

        end = datetime.fromisoformat(x["end"]) if x["end"] else None

        return (end - now) if start <= now else (start - now)

    upcoming.sort(key=lambda x: remaining(x))

    return {"events": upcoming}


# ------------------------index.html

@app.get("/")
async def index():

    return FileResponse("index.html")
