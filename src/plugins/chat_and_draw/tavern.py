import os
import json
import base64
import re
from typing import Dict, Any, List, Optional
from PIL import Image
from . import config  # Load configuration before choosing the default character

CHARACTERS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "characters")
)

class TavernCard:
    def __init__(self, data: Dict[str, Any], filepath: str = ""):
        self.raw_data = data
        self.filepath = filepath

        # 兼容 Tavern V2 规范与传统扁平格式
        if "data" in data and isinstance(data["data"], dict):
            c_data = data["data"]
        else:
            c_data = data

        self.name: str = c_data.get("name", "未命名角色")
        self.description: str = c_data.get("description", "")
        self.personality: str = c_data.get("personality", "")
        self.scenario: str = c_data.get("scenario", "")
        self.first_mes: str = c_data.get("first_mes", "")
        self.mes_example: str = c_data.get("mes_example", "")
        self.system_prompt: str = c_data.get("system_prompt", "")

        # 世界书 (Lorebook / World Info)
        self.character_book: Optional[Dict[str, Any]] = c_data.get("character_book", None)
        if not self.character_book and "world_info" in c_data:
            self.character_book = c_data.get("world_info")

    @classmethod
    def from_file(cls, filepath: str) -> Optional["TavernCard"]:
        if not os.path.exists(filepath):
            return None

        ext = os.path.splitext(filepath)[1].lower()

        if ext == ".json":
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls(data, filepath)
            except Exception as e:
                print(f"[Tavern] 解析 JSON 角色卡失败 {filepath}: {e}")
                return None

        elif ext == ".png":
            try:
                with Image.open(filepath) as img:
                    # 酒馆规范：元数据储存在 'chara' 标签中 (base64 编码的 utf-8 json 字符串)
                    chara_raw = img.info.get("chara")
                    if chara_raw:
                        decoded = base64.b64decode(chara_raw).decode("utf-8")
                        data = json.loads(decoded)
                        return cls(data, filepath)
                    else:
                        print(f"[Tavern] PNG 图片中未发现 'chara' 酒馆元数据: {filepath}")
                        return None
            except Exception as e:
                print(f"[Tavern] 解析 PNG 角色卡失败 {filepath}: {e}")
                return None

        return None

    def scan_lorebook(self, text_to_scan: str) -> List[str]:
        """
        扫描文本中的关键词，返回触发的世界书词条内容列表
        """
        if not self.character_book:
            return []

        if isinstance(self.character_book, list):
            entries = self.character_book
        elif isinstance(self.character_book, dict):
            entries = self.character_book.get("entries", [])
        else:
            return []

        matched_contents = []
        text_lower = text_to_scan.lower()

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if not entry.get("enabled", True):
                continue

            keys = entry.get("keys", [])
            if isinstance(keys, str):
                keys = [k.strip() for k in keys.split(",") if k.strip()]

            matched = False
            for key in keys:
                if not key:
                    continue
                # 不区分大小写匹配
                if key.lower() in text_lower:
                    matched = True
                    break

            if matched:
                content = entry.get("content", "").strip()
                if content:
                    matched_contents.append(content)

        return matched_contents

class TavernManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TavernManager, cls).__new__(cls)
            cls._instance.cards: Dict[str, TavernCard] = {}
            cls._instance.active_card_name: Optional[str] = None
            cls._instance.ensure_characters_dir()
            cls._instance.reload_cards()
        return cls._instance

    def ensure_characters_dir(self):
        if not os.path.exists(CHARACTERS_DIR):
            os.makedirs(CHARACTERS_DIR, exist_ok=True)

    def reload_cards(self):
        self.cards.clear()
        self.ensure_characters_dir()

        for filename in os.listdir(CHARACTERS_DIR):
            filepath = os.path.join(CHARACTERS_DIR, filename)
            if os.path.isfile(filepath):
                card = TavernCard.from_file(filepath)
                if card:
                    card_id = os.path.splitext(filename)[0]
                    self.cards[card_id] = card
                    # 同时支持通过卡片名索引
                    self.cards[card.name] = card

        # 默认优先加载环境变量配置的角色或 'ling'
        default_char = os.getenv("DEFAULT_CHARACTER", "ling")
        if not self.active_card_name or self.active_card_name not in self.cards:
            if default_char in self.cards:
                self.active_card_name = default_char
            elif "ling" in self.cards:
                self.active_card_name = "ling"
            elif "玲" in self.cards:
                self.active_card_name = "玲"
            elif self.cards:
                self.active_card_name = next(iter(self.cards))

        print(f"[Tavern] 已加载 {len(self.cards)} 个角色卡标识: {list(self.cards.keys())}，当前默认角色: {self.active_card_name}")

    def get_active_card(self) -> Optional[TavernCard]:
        if self.active_card_name and self.active_card_name in self.cards:
            return self.cards[self.active_card_name]
        default_char = os.getenv("DEFAULT_CHARACTER", "ling")
        if default_char in self.cards:
            return self.cards[default_char]
        if "ling" in self.cards:
            return self.cards["ling"]
        if "玲" in self.cards:
            return self.cards["玲"]
        if self.cards:
            first_key = next(iter(self.cards))
            return self.cards[first_key]
        return None

    def find_card(self, name_or_id: str) -> Optional[TavernCard]:
        self.reload_cards()
        target = name_or_id.strip()

        # 1. 精确匹配（文件名或角色原名）
        if target in self.cards:
            return self.cards[target]

        # 2. 忽略大小写匹配
        for k, card in self.cards.items():
            if k.lower() == target.lower() or card.name.lower() == target.lower():
                return card

        # 3. 包含模糊匹配（例如输入 "汐媛" 或 "小萱"）
        for k, card in self.cards.items():
            if target.lower() in k.lower() or target.lower() in card.name.lower():
                return card

        return None

    def switch_card(self, name_or_id: str) -> Optional[TavernCard]:
        """Compatibility lookup; active roles belong to sessions, never global state."""
        return self.find_card(name_or_id)

    def list_cards(self) -> List[str]:
        self.reload_cards()
        seen = set()
        result = []
        for filename in os.listdir(CHARACTERS_DIR):
            card_id = os.path.splitext(filename)[0]
            card = self.cards.get(card_id)
            if card and card.name not in seen:
                seen.add(card.name)
                result.append(f"【{card.name}】 -> 指令：/switch {card.name}")
        return result

    def build_system_prompt(self, card: TavernCard, recent_context: str = "") -> str:
        """
        按酒馆规范将角色卡要素组合为 System Prompt，并挂载机器人生图与语音核心约束
        """
        char_name = card.name
        user_name = "你"

        # 替换酒馆标准宏变量 {{char}} 与 {{user}}
        def replace_macros(text: str) -> str:
            if not text:
                return ""
            return text.replace("{{char}}", char_name).replace("{{user}}", user_name).replace("<USER>", user_name).replace("<CHAR>", char_name)

        desc = replace_macros(card.description)
        personality = replace_macros(card.personality)
        scenario = replace_macros(card.scenario)
        mes_example = replace_macros(card.mes_example)

        lore_entries = card.scan_lorebook(recent_context)
        lore_section = ""
        if lore_entries:
            lore_cleaned = [replace_macros(e) for e in lore_entries]
            lore_section = "\n【世界观与背景知识 (World Info)】\n" + "\n".join(f"- {e}" for e in lore_cleaned) + "\n"

        tavern_prompt = f"""你是 {char_name}。请完全沉浸于该角色的性格与设定中，以第一人称与用户聊天。

【角色人设 (Character Description)】
{desc}

【性格特征 (Personality)】
{personality}

【当前情景与关系 (Scenario)】
{scenario}
{lore_section}
【极其严格的对话风格约束】
1. **强制使用中文**：无论原角色卡用何种语言编写，你**必须全程使用自然、地道、富有角色个性的简体中文**进行对话和心理描写。
2. **适度分段发送**：单句话包含 5-20 个字。如果你想连发，可以用回车换行（\n）拆分，但**每次回复最多只允许 1 到 3 行**，绝不能像小说一样大段刷屏！
3. **口语化且自然**：以聊天软件（QQ）私聊的口吻说话，融入角色设定的语气。
4. **拒绝冷场**：当对方回复简短时，必须主动依据当前人设抛出新话题。

【核心强制功能指令（最高优先级）】
1. **生图指令**：当用户要求你：发自拍、看照片、换衣服、看全身照、发插画等，你**必须**在回复文字的末尾输出隐藏英文生图标签：<draw: 英文标签1, 英文标签2, ...>
   - 必须结合你当前的人设外貌和服装特征输出符合 Danbooru 风格的英文 Tag。
   - 示例：<draw: 1girl, solo, cute, gentle smile, looking at viewer, casual clothes>
2. **发送语音指令**：当用户要求你发语音（例如“发语音”、“说句话”），或者当前语境非常适合语音表达时，你**必须**在回复结尾附带标签：<voice>
3. **表情包指令**：如果你想发送表情包，在末尾附带：<sticker: 分类名> (分类仅限 cry, pout, smug, facepalm, laugh, happy, stare, think, shrug, shy；例如 <sticker: shy>)

【回复示例】
用户：发张自拍看看嘛
{char_name}：真是拿你没办法，等我换好衣服拍给你~ <draw: 1girl, solo, beautiful, gentle smile, looking at viewer, casual clothes>

用户：给我发条语音听听嘛
{char_name}：笨蛋，听好咯，今天可不准惹我生气！<voice>
"""
        if card.system_prompt:
            tavern_prompt += "\n【角色补充要求】\n" + replace_macros(card.system_prompt) + "\n"

        if mes_example:
            tavern_prompt += f"\n【对话样例 (Dialogue Examples)】\n{mes_example}\n"

        return tavern_prompt

tavern_manager = TavernManager()
