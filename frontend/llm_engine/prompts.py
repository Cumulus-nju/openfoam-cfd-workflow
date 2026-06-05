"""
Prompt templates for the LLM intelligence engine.

Each prompt is a function that builds a message list for the specific task.
All prompts produce structured JSON output for reliable parsing.

Design principles:
1. Be explicit about the JSON schema expected
2. Provide domain knowledge (Chinese campus norms) in the system prompt
3. Use few-shot examples for complex tasks
4. Keep prompts concise — Qwen2.5-1.5B has limited context
"""
from __future__ import annotations

from typing import Any, Dict, List


# ═══════════════════════════════════════════════════════════════════════════════
# System prompt base — shared domain knowledge
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_BASE = """你是UrbanWind CFD的城市建模助手。你的职责是帮助用户构建城市风场模拟的几何模型。

你的领域知识：
- 中国大学校园建筑规范：教学楼通常4-6层(13-20m)，宿舍6-7层(20-23m)，食堂1-2层(6-10m，层高较高)，图书馆为地标建筑(20-40m)，体育馆大跨度(8-15m)
- 标准层高：住宅/宿舍3.0m，教学楼3.3m，办公楼3.3m，实验室3.6m，食堂4.0m
- 建筑形态：教学楼多为矩形板楼或围合式，宿舍多为条形板楼，图书馆体量较大且位置居中
- 中国建筑多为平顶，部分老校区有坡顶

输出规则：
- 所有回复必须为纯JSON，不要包裹在markdown代码块中
- 不要添加解释性文字
- 对不确定的属性，设置较低的confidence值(0.1-0.5)并使用合理的默认值"""


# ═══════════════════════════════════════════════════════════════════════════════
# Task: Infer building attributes from limited data
# ═══════════════════════════════════════════════════════════════════════════════

INFER_BUILDING_ATTRS = lambda buildings_json: [
    {"role": "system", "content": SYSTEM_BASE + """

你要根据建筑的类型标签和可用信息，推断每栋建筑的完整属性。

推断规则：
1. 如果提供了building_type，使用对应的默认高度和层数
2. 如果提供了name，从名称推断类型（如"逸夫楼"可能是教学楼）
3. 如果只提供了height但没提供num_floors，按层高3.3m计算
4. 如果只有轮廓(OSM数据常见)，使用默认值并设置low confidence
5. 所有建筑的roof_type默认为flat

返回JSON数组，每个元素对应输入中的一栋建筑。"""},
    {"role": "user", "content": f"""以下是需要推断的建筑数据（GeoJSON features数组）：

{buildings_json}

请返回补全后的buildings JSON数组。每栋建筑必须包含这些字段：
- building_type: "teaching"|"dormitory"|"canteen"|"library"|"office"|"lab"|"gymnasium"|"other"
- height: 高度(米), 数字
- num_floors: 层数, 整数
- roof_type: "flat"|"pitched"|"arched"|"unknown"
- name_zh: 中文名称(如果能推断)
- confidence: 推断置信度(0-1), 数字
- reasoning: 推断理由, 简短中文

只返回JSON数组，不要其他内容。"""},
]


# ═══════════════════════════════════════════════════════════════════════════════
# Task: Normalize multi-source geometry to unified format
# ═══════════════════════════════════════════════════════════════════════════════

NORMALIZE_GEOMETRY = lambda raw_data_json, source_type: [
    {"role": "system", "content": SYSTEM_BASE + """

你要将不同来源的原始建筑数据标准化为统一的GeoJSON格式。

任务：
1. 识别输入中的建筑实体
2. 为每个实体生成标准属性（building_type, height, num_floors, roof_type, name_zh）
3. 补齐缺失字段
4. 标记source为来源类型
5. 标记推断字段的confidence"""},
    {"role": "user", "content": f"""数据来源: {source_type}

原始数据: {raw_data_json}

请返回标准化的GeoJSON FeatureCollection，格式如下：
{{
  "type": "FeatureCollection",
  "features": [
    {{
      "type": "Feature",
      "geometry": {{"type": "Polygon", "coordinates": [[[x,y], ...]]}},
      "properties": {{
        "building_type": "...",
        "height": 数字,
        "num_floors": 整数,
        "roof_type": "...",
        "name_zh": "...",
        "confidence": 0-1,
        "source": "{source_type}"
      }}
    }}
  ]
}}

只返回JSON。"""},
]


# ═══════════════════════════════════════════════════════════════════════════════
# Task: Interactive natural language editing
# ═══════════════════════════════════════════════════════════════════════════════

INTERACTIVE_EDIT = lambda current_state_json, user_instruction: [
    {"role": "system", "content": SYSTEM_BASE + """

你是一个交互式建筑模型编辑器。用户会用自然语言描述他们想对建筑模型做什么修改。

你需要：
1. 理解用户的修改意图
2. 生成一个修改操作列表
3. 确认修改后的状态

支持的操作类型：
- update: 修改建筑属性（高度、类型、名称等）
- add: 添加新建筑
- delete: 删除建筑
- move: 移动建筑位置
- resize: 调整建筑尺寸
- add_bike: 添加单车站点
- delete_bike: 删除单车站点"""},
    {"role": "user", "content": f"""当前建筑模型状态（GeoJSON）：

{current_state_json}

用户指令: {user_instruction}

请分析指令并返回修改操作。返回JSON格式：
{{
  "understood": true/false,
  "explanation": "对指令的理解（中文）",
  "operations": [
    {{
      "action": "update"|"add"|"delete"|"move"|"resize"|"add_bike"|"delete_bike",
      "target_id": "目标建筑ID（update/delete/move/resize时）",
      "params": {{
        // 具体修改参数
      }}
    }}
  ],
  "updated_state": {{ /* 修改后的完整GeoJSON */ }}
}}

如果指令模糊或无法执行，设置understood=false并解释原因。

只返回JSON。"""},
]


# ═══════════════════════════════════════════════════════════════════════════════
# Task: Smart bike station placement
# ═══════════════════════════════════════════════════════════════════════════════

PLACE_BIKES = lambda buildings_json, num_bikes: [
    {"role": "system", "content": SYSTEM_BASE + """

你是共享单车选址专家。你需要基于建筑布局，合理安排单车站点位置。

选址原则（科研设计）：
1. open区域：开阔地带，作为基准风速参考点——放置在建筑群外围无明显遮挡处
2. wake区域：建筑背风区——放置在下风向建筑后方，捕捉尾流减速效应
3. canyon区域：街道峡谷——放置在间距较窄的平行建筑之间，捕捉狭管增速效应
4. corner区域：转角剪切区——放置在建筑角部附近，捕捉分离剪切效应

假设风向为北风（从+y吹向-y）。建筑北侧为上风向，南侧为下风向。

你需要：
1. 识别建筑布局中的四种典型风环境
2. 为每类放置合理数量的站点
3. 每个站点返回坐标(cx, cy)和category"""},
    {"role": "user", "content": f"""建筑数据（GeoJSON）：

{buildings_json}

需要放置约 {num_bikes} 个单车站点，均匀分布在四种风环境类型中。

返回JSON：
{{
  "bikes": [
    {{
      "cx": 数字,
      "cy": 数字,
      "category": "open"|"wake"|"canyon"|"corner",
      "reasoning": "放置理由（简短中文）"
    }}
  ]
}}

domain范围从建筑bbox向外扩展30米。建筑北侧为上风向。

只返回JSON。"""},
]


# ═══════════════════════════════════════════════════════════════════════════════
# Task: Parse free-text layout description
# ═══════════════════════════════════════════════════════════════════════════════

PARSE_TEXT_LAYOUT = lambda user_text: [
    {"role": "system", "content": SYSTEM_BASE + """

你要从用户的自由文本描述中提取建筑布局信息。

用户可能会描述：
- 校园/区域的整体大小
- 每栋建筑的位置（相对于整个区域）、尺寸、层数/高度、用途
- 需要放置多少单车点位

当用户没有明确指定位置时，你需要合理推断建筑在区域内的布局。
使用你的建筑领域知识推断缺失的属性。

返回完整的GeoJSON FeatureCollection。"""},
    {"role": "user", "content": f"""用户描述：

{user_text}

请提取并返回完整的建筑布局GeoJSON。每栋建筑需要：
- 合理的多边形坐标（在假设的domain范围内）
- 完整的properties（building_type, height, num_floors, roof_type, name_zh, confidence）

domain默认300×300米，如果有指定则使用指定大小。
建筑默认矩形。
如果可能，也返回建议的单车站点位置。

返回JSON格式：
{{
  "domain": [x_min, y_min, x_max, y_max],
  "features": [GeoJSON Feature数组],
  "bikes": [{{"cx": x, "cy": y, "category": "..."}}]  // 如果有
}}

只返回JSON。"""},
]
