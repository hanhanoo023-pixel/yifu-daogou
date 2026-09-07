from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from backend.currency import CurrencyCode
from backend.schemas import (
    IntentResult,
    IntentType,
    NormalizedFilters,
    ParsedShoppingRequest,
    PendingAction,
    PendingActionType,
    PendingProductOption,
    RecommendationRequest,
    SlotOperation,
    SlotOperationType,
)
from backend.usage_logger import log_api_usage
from backend.style_matcher import load_scene_rules


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_ENV_PATH = PROJECT_ROOT / ".env"
INTENT_TAXONOMY_PATH = PROJECT_ROOT / "configs" / "intent_taxonomy.yaml"
UNSUPPORTED_OCCASION_WARNING = "UNSUPPORTED_OCCASION"
UNSUPPORTED_OCCASION_MESSAGE = (
    "目前没找到符合这类场景的衣服呢，要不您换个表述，或试试其他场景的衣服？"
)


@dataclass(frozen=True)
class IntentApiSettings:
    api_url: str
    api_key: str
    model: str
    disable_thinking: bool

SYSTEM_PROMPT = """你是服装导购的结构化意图解析器。只输出合法 JSON，不要输出解释或 Markdown。

输出字段：primary_intent、secondary_intents、confidence、clarify_needed、clarify_question、slot_operations、requested_fields、product_references、warnings。
slot_operations 只包含本轮明确表达的变化，每项结构为 {"field":字段,"operation":SET|ADD|REMOVE|CLEAR|KEEP,"value":值}。SET替换；CLEAR清空；KEEP只用于用户明确说保持不变。不要重复未变化的历史条件。

数组槽位仅限：occasions、style_preferences、body_goals、weather、excluded_categories、excluded_colors、excluded_materials、excluded_brands、excluded_styles、negative_colors、excluded_product_ids。
数组槽位的 SET、ADD、REMOVE 的 value 必须是 JSON 数组，即使只有一个值也必须使用数组；严禁输出字符串、数字或 null。ADD、REMOVE 不得用于其他槽位。
正确：{"field":"occasions","operation":"ADD","value":["beach"]}
正确：{"field":"negative_colors","operation":"ADD","value":["pink"]}
错误：{"field":"occasions","operation":"ADD","value":"beach"}
错误：{"field":"negative_colors","operation":"ADD","value":"pink"}

标准值：
- category：top、dress、pants、shorts、skirt、outerwear、sweater、swimwear、underwear、shoes、bag、jewelry、watch、accessory、set、costume
- product_scope：clothing、footwear、bags、jewelry、accessories、all；用户只说“衣服/服装”时设置 clothing，只说“鞋/包/首饰/配饰”时设置对应范围
- color：yellow、black、white、blue、red、green、pink、purple、brown、gray、beige、orange、silver、gold、multicolor；color_depth：light、medium、dark
- size：XS、S、M、L、XL、2XL、3XL、4XL；season：spring、summer、autumn、winter；price_currency：CNY、USD
- min_rating：最低商品评分，仅允许 0 到 5 的数字。例如“评分4.5以上”对 min_rating SET 4.5
- material：cotton、wool、polyester、leather、silk、lace、denim、linen
- occasions：__OCCASION_VALUES__
- style_preferences：gentle、casual、minimalist、formal、sporty、retro、streetwear、elegant
- body_goals：elongate（显高/拉长比例）、streamline（显瘦/线条利落）、tummy_coverage（遮腹）、shoulder_balance（平衡宽肩）、leg_balance（修饰腿型）
- 其他槽位：subcategory、gender、age_group、fit、pattern、sleeve_length、garment_length、neckline、weather
- 排除槽位：excluded_categories、excluded_colors、excluded_materials、excluded_brands、excluded_styles、excluded_product_ids；软负向颜色：negative_colors

主意图仅允许：RECOMMEND_PRODUCT、BROWSE_PRODUCT、REFINE_FILTERS、CLEAR_FILTERS、RESET_SESSION、REQUEST_ALTERNATIVE、COMPARE_PRODUCTS、VIEW_PRODUCT、EXPLAIN_PRODUCT、ASK_PRICE、ASK_MATERIAL、ASK_STOCK、ASK_SIZE、ASK_COLOR、ASK_BRAND、ASK_CARE、ASK_STYLE、ASK_OCCASION、OUTFIT_ADVICE、SELECT_PERSON_IMAGE、UPLOAD_PERSON_IMAGE、START_TRY_ON、CHANGE_BACKGROUND、SAVE_RESULT、CONTACT_SALES、PURCHASE_PRODUCT、OPEN_PRODUCT_DETAIL、NAVIGATE_APP、ASK_TREND、SEARCH_EXTERNAL_PRODUCT、ASK_NEW_ARRIVAL、PREFERENCE_UPDATE、MEMORY_QUERY、MEMORY_DELETE、GREETING、HELP、THANKS、CONFIRM、DENY、SMALL_TALK、OUT_OF_SCOPE、UNKNOWN。

规则：
1. “适合夏天去海边”设置 season=summer，并向 occasions ADD beach，不能只保留季节。
2. “300元以内”设置 max_price=300、price_currency=CNY；“不要粉色”向 excluded_colors ADD pink；“不太喜欢粉色”向 negative_colors ADD pink。
3. “换成蓝色”是 REFINE_FILTERS，对 color SET blue；“不限制季节”对 season CLEAR；“换一件”是 REQUEST_ALTERNATIVE。
4. “第二件有L码吗，没有就换件白色的”以 ASK_STOCK 为主，次意图包含 REQUEST_ALTERNATIVE，并记录商品指代。
5. “第一件和第三件哪个更适合面试”是 COMPARE_PRODUCTS，向 occasions ADD interview，保留两个商品指代。
6. 事实询问缺少商品上下文时需要澄清；比较不足两个商品时需要澄清。
7. 与服装导购无关时为 OUT_OF_SCOPE；无法判断时为 UNKNOWN。
8. 不要补出用户没有表达的颜色、尺码、价格、品牌或材质。
9. field 必须严格来自配置中的合法槽位，禁止创造新字段。
10. “怎么搭/配什么”是 OUTFIT_ADVICE；“最近流行/热门/卖得好”是 ASK_TREND。
11. 明确要去淘宝、小红书等外部平台找商品是 SEARCH_EXTERNAL_PRODUCT；“最新款/上新”是 ASK_NEW_ARRIVAL。
12. “记住我喜欢……/以后不要……”是 PREFERENCE_UPDATE；“你记得我什么”是 MEMORY_QUERY；“忘掉我的偏好”是 MEMORY_DELETE。
13. 用户说“今天想看看 Nike 的衣服”时，如果没有指定外部平台或实时新品，应从当前商品库推荐，设置 brand=Nike 和 product_scope=clothing。
14. 用户只回复“要”“都要”“继续”“可以”等短句时，必须结合最近一条助手问题判断其承接的具体意图；如果助手同时提供了多个选项且用户说“都要”，把这些意图分别放入主意图和次意图，不要只输出 CONFIRM。
15. “浅色/浅一点/亮一点”对 color_depth SET light；“深色/深一点/暗一点”对 color_depth SET dark。颜色深浅不是具体颜色，不得据此猜测 color、excluded_colors 或 negative_colors。
16. secondary_intents、slot_operations、requested_fields、product_references、warnings 必须始终输出 JSON 数组；没有内容时输出 []，禁止输出 null。
17. 用户明确要找某个场景下穿的衣服，但该场景无法映射到 occasions 合法值时，不得猜测或输出非法场景；clarify_needed=true，clarify_question 输出“目前没找到符合这类场景的衣服呢，要不您换个表述，或试试其他场景的衣服？”，warnings 加入 "UNSUPPORTED_OCCASION"。
18. 上传、选择人物照片分别是 UPLOAD_PERSON_IMAGE、SELECT_PERSON_IMAGE；“试穿/上身看看”是 START_TRY_ON。
19. 换背景、保存试穿图、咨询导购、购买当前商品、查看当前商品详情分别是 CHANGE_BACKGROUND、SAVE_RESULT、CONTACT_SALES、PURCHASE_PRODUCT、OPEN_PRODUCT_DETAIL。
20. “打开服装库/进入试穿页”等明确页面跳转是 NAVIGATE_APP。APP 操作意图不得改写购物筛选槽位。
21. “显高、显瘦、遮肚子/遮腹、肩宽想修饰、腿型想修饰”分别向 body_goals ADD elongate、streamline、tummy_coverage、shoulder_balance、leg_balance。它们是基于版型证据的推荐目标，不得承诺必然改变身材。
"""

SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "__OCCASION_VALUES__",
    "、".join(load_scene_rules()),
)


RULE_INTENT_PATTERNS: tuple[tuple[IntentType, re.Pattern[str]], ...] = (
    (IntentType.SAVE_RESULT, re.compile(r"(?:保存|下载|收藏).{0,8}(?:试穿|效果|结果)?(?:图|图片|照片)|保存(?:试穿)?结果")),
    (IntentType.CHANGE_BACKGROUND, re.compile(r"(?:换|更换|修改|替换).{0,8}背景|背景.{0,4}(?:换成|改成|替换成)")),
    (IntentType.UPLOAD_PERSON_IMAGE, re.compile(r"(?:上传|添加|扫码上传|拍照上传).{0,10}(?:人物|人像|本人|我的)?(?:照片|图片|人像)")),
    (IntentType.SELECT_PERSON_IMAGE, re.compile(r"(?:选择|选用|使用|换一张).{0,10}(?:人物|人像|本人|我的)?(?:照片|图片|人像)")),
    (IntentType.START_TRY_ON, re.compile(r"(?:AI)?试穿|试衣|上身(?:看看|看效果)|穿到我身上|试一下这件|这件试一下")),
    (IntentType.CONTACT_SALES, re.compile(r"(?:联系|咨询|呼叫|找).{0,6}(?:门店)?(?:导购|店员|销售|客服)")),
    (IntentType.PURCHASE_PRODUCT, re.compile(r"(?:购买|买下|下单|结算)(?:这件|当前|选中的商品)|立即购买|这件怎么买")),
    (IntentType.OPEN_PRODUCT_DETAIL, re.compile(r"(?:查看|打开|进入).{0,6}(?:当前|这件|商品)?详情|商品详情")),
    (IntentType.NAVIGATE_APP, re.compile(r"(?:打开|进入|跳转到).{0,8}(?:服装库|商品库|试穿页|人像页|收藏页)")),
    (IntentType.MEMORY_DELETE, re.compile(r"(?:忘掉|删除|清除|别记)(?:我的)?(?:偏好|记忆)")),
    (IntentType.MEMORY_QUERY, re.compile(r"(?:你记得我|记住了我|我的偏好是|记得我什么)")),
    (IntentType.PREFERENCE_UPDATE, re.compile(r"(?:记住|以后都|我一直喜欢|以后不要)")),
    (IntentType.ASK_TREND, re.compile(r"(?:最近|今年|当下)[^\uff0c\u3002\uff1b]*(?:流行|热门|火|爆款|卖得好|趋势)")),
    (IntentType.SEARCH_EXTERNAL_PRODUCT, re.compile(r"(?:淘宝|小红书|天猫|京东|拼多多)[^\uff0c\u3002\uff1b]*(?:找|搜|看看|商品|衣服|穿搭)")),
    (IntentType.ASK_NEW_ARRIVAL, re.compile(r"(?:最新款|新款|上新|新品)")),
    (IntentType.OUTFIT_ADVICE, re.compile(r"(?:怎么搭|如何搭|配什么|穿搭建议|怎么穿)")),
    (IntentType.COMPARE_PRODUCTS, re.compile(r"(?:对比|比较|哪(?:一)?件更|哪个好|区别)")),
    (IntentType.ASK_STOCK, re.compile(r"(?:有货吗|还有货吗|库存(?:多少|怎么样|有吗)|有[XSML0-9]+码吗)", re.IGNORECASE)),
    (IntentType.ASK_PRICE, re.compile(r"(?:多少钱|什么价|价格(?:是)?多少|价位(?:是多少)?)")),
    (IntentType.ASK_MATERIAL, re.compile(r"(?:什么材质|什么面料|材质(?:是)?什么|面料(?:是)?什么)")),
    (IntentType.ASK_SIZE, re.compile(r"(?:什么尺码|有哪些尺码|尺码(?:是)?什么|多大码)")),
    (IntentType.ASK_COLOR, re.compile(r"(?:什么颜色|有哪些颜色|颜色(?:是)?什么)")),
    (IntentType.ASK_BRAND, re.compile(r"(?:什么品牌|哪个牌子|品牌(?:是)?什么)")),
    (IntentType.ASK_CARE, re.compile(r"(?:怎么洗|如何清洗|能机洗吗|洗涤|保养)")),
    (IntentType.ASK_STYLE, re.compile(r"(?:什么风格|属于什么风格)")),
    (IntentType.ASK_OCCASION, re.compile(r"(?:适合什么场合|什么场合穿)")),
    (IntentType.REQUEST_ALTERNATIVE, re.compile(r"(?:换一件|换一款|再来一|看别的|重新推荐)")),
    (IntentType.EXPLAIN_PRODUCT, re.compile(r"(?:这件怎么样|介绍一下这件|为什么推荐)")),
    (IntentType.VIEW_PRODUCT, re.compile(r"(?:看看|打开|查看)第?\s*(?:\d+|[一二三四五六七八九十]+)\s*件")),
    (IntentType.RESET_SESSION, re.compile(r"(?:重新开始|重新来过|重置对话|清空对话)")),
    (IntentType.CLEAR_FILTERS, re.compile(r"(?:清空|取消|去掉)所有?(?:筛选|条件|要求)")),
    (IntentType.REFINE_FILTERS, re.compile(r"(?:换成|改成|改为|不限制|去掉|不要这个条件|保持不变|浅色|浅一点|亮一点|深色|深一点|暗一点|显高|显瘦|遮腹|遮肚|肩宽|腿型)")),
    (IntentType.OUT_OF_SCOPE, re.compile(r"(?:订机票|查天气|写代码|播放音乐|股票行情|看病)")),
    (IntentType.BROWSE_PRODUCT, re.compile(r"(?:想)?看看|浏览|有哪些")),
    (IntentType.RECOMMEND_PRODUCT, re.compile(r"(?:推荐|想买|想找|找(?:一件|一款|一条|一双|一些)?|需要一|帮我选)")),
    (IntentType.HELP, re.compile(r"(?:怎么用|你能做什么|帮助|功能)")),
    (IntentType.THANKS, re.compile(r"(?:谢谢|感谢|辛苦了)")),
    (IntentType.GREETING, re.compile(r"^(?:你好|嗨|hello|hi)[！!。.]?$", re.IGNORECASE)),
    (IntentType.CONFIRM, re.compile(r"^(?:确认|好的|可以|就这样|对)[！!。.]?$")),
    (IntentType.DENY, re.compile(r"^(?:不用|不要了|算了|不对|取消)[！!。.]?$")),
    (IntentType.SMALL_TALK, re.compile(r"^(?:你叫什么|你是谁|聊聊天|在吗)[？?！!。.]?$")),
)

CONTEXT_REQUIRED_INTENTS = {
    IntentType.ASK_PRICE,
    IntentType.ASK_STOCK,
    IntentType.ASK_MATERIAL,
    IntentType.ASK_SIZE,
    IntentType.ASK_COLOR,
    IntentType.ASK_BRAND,
    IntentType.ASK_CARE,
    IntentType.ASK_STYLE,
    IntentType.ASK_OCCASION,
    IntentType.OUTFIT_ADVICE,
    IntentType.EXPLAIN_PRODUCT,
    IntentType.VIEW_PRODUCT,
}

CONTEXTUAL_CONTINUATION_PATTERN = re.compile(
    r"^(?:要|都要|全都要|两个都要|想要|好|好的|可以|行|继续|都说说|都介绍一下|都看看)[吧呀啊呢～！!。.]?$"
)

CONTEXTUAL_RECOMMENDATION_CONFIRMATION_PATTERN = re.compile(
    r"^(?:要|可以|好|好的|行|试试|要的|没问题)[吧呀啊呢～！!。.]?$"
)

CONTEXTUAL_RECOMMENDATION_ACTION_PATTERN = re.compile(
    r"(?:再|重新)?(?:帮你)?(?:"
    r"筛(?:一遍|选)?|推荐|找|"
    r"看(?:看|下)?(?:有没有)?[^。！？!?]{0,16}(?:其他|别的|更多|另外)"
    r")"
)

CONTEXTUAL_ALTERNATIVE_RECOMMENDATION_PATTERN = re.compile(
    r"(?:其他|别的|更多|另外|再(?:看|来)(?:一|几))"
)

CLOTHING_USAGE_PATTERN = re.compile(
    r"(?:穿|衣服|服装|穿搭|上衣|裤子|裙子|连衣裙|鞋子|包包)"
)

CONTEXTUAL_RECOMMENDATION_OFFER_PATTERN = re.compile(
    r"(?:要不要|要试试|想试试|需要我|是否|可以[^。！？!?]*(?:吗|呢))"
)

COMPARISON_PREFERENCE_PATTERN = re.compile(
    r"(?:你更(?:在意|看重|偏向|喜欢)(?:的是)?|你想(?:选|要))"
    r"(?P<first>.+?)(?:，|,|、)?还是"
    r"(?P<second>.+?)(?:呢|吗)?[？?]"
)

CURRENT_PRODUCT_RECOMMENDATION_PATTERN = re.compile(
    r"^(?:那)?(?:你|您)?(?:更)?(?:推荐|建议)(?:我)?(?:选|买|要)?"
    r"哪(?:一)?(?:件|个|款)(?:呢|吗)?[？?。！!]*$"
    r"|^(?:那)?(?:我)?(?:该|应该|到底)?选哪(?:一)?(?:件|个|款)"
    r"(?:呢|吗)?[？?。！!]*$"
)

COMPARISON_PREFERENCE_KEYWORDS = (
    "上镜",
    "活泼",
    "俏皮",
    "可爱",
    "蕾丝",
    "精致",
    "温柔",
    "氛围",
    "随性",
    "飘逸",
    "舒适",
    "方便",
    "休闲",
    "长裙",
    "宽松",
    "透气",
    "预算",
    "价格",
    "性价比",
    "材质",
    "颜色",
    "版型",
    "评分",
    "评论",
)

CONTEXTUAL_TOPIC_PATTERNS: tuple[tuple[IntentType, re.Pattern[str]], ...] = (
    (IntentType.ASK_PRICE, re.compile(r"(?:价格|多少钱|预算)")),
    (IntentType.ASK_MATERIAL, re.compile(r"(?:材质|面料)")),
    (IntentType.ASK_STOCK, re.compile(r"(?:库存|有货)")),
    (IntentType.ASK_SIZE, re.compile(r"(?:尺码|码数|大小)")),
    (IntentType.ASK_COLOR, re.compile(r"颜色")),
    (IntentType.ASK_STYLE, re.compile(r"(?:版型|剪裁|廓形|风格)")),
    (IntentType.ASK_OCCASION, re.compile(r"(?:适合(?:什么|哪些)场合|哪些场合)")),
    (IntentType.OUTFIT_ADVICE, re.compile(r"(?:搭配|穿搭|配什么|穿法)")),
)

CONTEXTUAL_PRODUCT_OPTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pants", "clothing", ("裤子", "长裤", "裤装", "牛仔裤")),
    ("shorts", "clothing", ("短裤",)),
    ("shoes", "footwear", ("鞋子", "鞋", "鞋履")),
    ("bag", "bags", ("包包", "包", "箱包", "手提包", "背包")),
    ("dress", "clothing", ("连衣裙",)),
    ("skirt", "clothing", ("裙子", "半身裙")),
    ("top", "clothing", ("上衣", "衬衫", "背心", "T恤")),
    ("outerwear", "clothing", ("外套", "夹克", "大衣")),
    ("sweater", "clothing", ("毛衣", "卫衣")),
    ("swimwear", "clothing", ("泳装", "泳衣")),
    ("jewelry", "jewelry", ("首饰", "珠宝")),
    ("watch", "jewelry", ("手表",)),
    ("accessory", "accessories", ("配饰",)),
)

PRODUCT_SWITCH_CLEAR_FIELDS = (
    "subcategory",
    "color",
    "size",
    "brand",
    "material",
    "fit",
    "pattern",
    "sleeve_length",
    "garment_length",
    "neckline",
    "excluded_product_ids",
)

RECOMMENDATION_INTENTS = {
    IntentType.RECOMMEND_PRODUCT,
    IntentType.BROWSE_PRODUCT,
    IntentType.REQUEST_ALTERNATIVE,
    IntentType.REFINE_FILTERS,
}

APP_ACTION_INTENTS = {
    IntentType.SELECT_PERSON_IMAGE,
    IntentType.UPLOAD_PERSON_IMAGE,
    IntentType.START_TRY_ON,
    IntentType.CHANGE_BACKGROUND,
    IntentType.SAVE_RESULT,
    IntentType.CONTACT_SALES,
    IntentType.PURCHASE_PRODUCT,
    IntentType.OPEN_PRODUCT_DETAIL,
    IntentType.NAVIGATE_APP,
}

LIST_FIELDS = {
    "occasions",
    "style_preferences",
    "body_goals",
    "weather",
    "excluded_categories",
    "excluded_colors",
    "excluded_materials",
    "excluded_brands",
    "excluded_styles",
    "negative_colors",
    "excluded_product_ids",
}

CHINESE_RANKS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}

STYLE_TERMS = {
    "gentle": ("温柔", "甜美", "浪漫"),
    "casual": ("休闲", "日常风"),
    "minimalist": ("简约", "极简"),
    "formal": ("正式", "商务风"),
    "sporty": ("运动风",),
    "retro": ("复古",),
    "streetwear": ("街头",),
    "elegant": ("优雅",),
}

BODY_GOAL_TERMS = {
    "elongate": ("显高", "拉长比例", "拉长身形"),
    "streamline": ("显瘦", "修饰身形", "线条利落"),
    "tummy_coverage": ("遮腹", "遮肚子", "遮小肚子", "藏肚子"),
    "shoulder_balance": ("肩宽", "修饰肩部", "弱化肩宽", "平衡肩部"),
    "leg_balance": ("腿型", "修饰腿型", "遮腿", "腿不直"),
}

SEASON_TERMS = {
    "spring": ("春天", "春季"),
    "summer": ("夏天", "夏季"),
    "autumn": ("秋天", "秋季"),
    "winter": ("冬天", "冬季"),
}

PRODUCT_SCOPE_TERMS = {
    "clothing": ("衣服", "服装", "穿搭"),
    "footwear": ("鞋子", "鞋履", "一双鞋"),
    "bags": ("包包", "箱包", "手提包", "背包"),
    "jewelry": ("首饰", "珠宝", "手表"),
    "accessories": ("配饰",),
}

VALID_PRODUCT_CATEGORIES = {
    category for category, _, _ in CONTEXTUAL_PRODUCT_OPTIONS
} | {"underwear", "set", "costume"}

AGGREGATE_CATEGORY_SCOPES = {
    "clothing": "clothing",
    "clothes": "clothing",
    "衣服": "clothing",
    "服装": "clothing",
    "footwear": "footwear",
    "bags": "bags",
    "accessories": "accessories",
    "all": "all",
}

INTENT_RESPONSE_LIST_FIELDS = (
    "secondary_intents",
    "slot_operations",
    "requested_fields",
    "product_references",
    "warnings",
)

COLOR_TERMS = {
    "yellow": ("黄色", "yellow"),
    "black": ("黑色", "black"),
    "white": ("白色", "white"),
    "blue": ("蓝色", "blue"),
    "red": ("红色", "red"),
    "green": ("绿色", "green"),
    "pink": ("粉色", "pink"),
    "purple": ("紫色", "purple"),
    "brown": ("棕色", "brown"),
    "gray": ("灰色", "gray", "grey"),
    "beige": ("米色", "卡其色", "beige"),
    "orange": ("橙色", "orange"),
    "silver": ("银色", "silver"),
    "gold": ("金色", "gold"),
}

COLOR_DEPTH_TERMS = {
    "light": ("浅色", "浅一点", "亮一点", "偏浅", "明亮色"),
    "medium": ("中等深浅", "中色"),
    "dark": ("深色", "深一点", "暗一点", "偏深"),
}

SCENE_ADDITION_PATTERN = re.compile(
    r"(?:也|还要|同时|兼顾|以及|并且|除此之外|另外)"
)

VALID_PRODUCT_SCOPES = {*PRODUCT_SCOPE_TERMS, "all"}
VALID_COLOR_VALUES = {*COLOR_TERMS, "multicolor"}
VALID_SEASON_VALUES = set(SEASON_TERMS)
VALID_MATERIAL_VALUES = {
    "cotton",
    "wool",
    "polyester",
    "leather",
    "silk",
    "lace",
    "denim",
    "linen",
}
VALID_STYLE_VALUES = set(STYLE_TERMS)
VALID_BODY_GOAL_VALUES = set(BODY_GOAL_TERMS)
VALID_OCCASION_VALUES = set(load_scene_rules())

OCCASION_ENUM_ALIASES = {
    str(alias).strip().casefold(): scene
    for scene, rule in load_scene_rules().items()
    for alias in rule["aliases"]
}

LLM_ENUM_ALIASES = {
    "occasions": {
        **OCCASION_ENUM_ALIASES,
    },
    "style_preferences": {
        "sweet": "gentle",
        "romantic": "gentle",
        "minimal": "minimalist",
        "sport": "sporty",
        "vintage": "retro",
        "street": "streetwear",
        "business": "formal",
    },
    "excluded_styles": {
        "sweet": "gentle",
        "romantic": "gentle",
        "minimal": "minimalist",
        "sport": "sporty",
        "vintage": "retro",
        "street": "streetwear",
        "business": "formal",
    },
}

SCALAR_ENUM_VALUES = {
    "category": VALID_PRODUCT_CATEGORIES,
    "product_scope": VALID_PRODUCT_SCOPES,
    "color": VALID_COLOR_VALUES,
    "color_depth": set(COLOR_DEPTH_TERMS),
    "season": VALID_SEASON_VALUES,
    "material": VALID_MATERIAL_VALUES,
}

LIST_ENUM_VALUES = {
    "occasions": VALID_OCCASION_VALUES,
    "style_preferences": VALID_STYLE_VALUES,
    "body_goals": VALID_BODY_GOAL_VALUES,
    "excluded_categories": VALID_PRODUCT_CATEGORIES,
    "excluded_colors": VALID_COLOR_VALUES,
    "excluded_materials": VALID_MATERIAL_VALUES,
    "excluded_styles": VALID_STYLE_VALUES,
    "negative_colors": VALID_COLOR_VALUES,
}


def unsupported_llm_occasion_values(items: list[dict[str, Any]]) -> list[str]:
    unsupported: list[str] = []
    aliases = LLM_ENUM_ALIASES["occasions"]
    for item in items:
        if item.get("field") != "occasions" or item.get("operation") not in {
            SlotOperationType.SET.value,
            SlotOperationType.ADD.value,
        }:
            continue
        raw_values = item.get("value")
        if not isinstance(raw_values, list):
            continue
        for raw_value in raw_values:
            value = str(raw_value).strip().casefold()
            normalized = aliases.get(value, value)
            if normalized not in VALID_OCCASION_VALUES:
                unsupported.append(value)
    return list(dict.fromkeys(unsupported))


@lru_cache(maxsize=1)
def load_intent_taxonomy() -> dict[str, Any]:
    return dict(yaml.safe_load(INTENT_TAXONOMY_PATH.read_text(encoding="utf-8")))


def detect_rule_intents(message: str) -> list[IntentType]:
    detected: list[IntentType] = []
    for intent, pattern in RULE_INTENT_PATTERNS:
        if pattern.search(message) and intent not in detected:
            detected.append(intent)
    return detected


def detect_rule_intent(message: str) -> IntentType | None:
    detected = detect_rule_intents(message)
    return detected[0] if detected else None


def message_contains_scene_alias(message: str, alias: object) -> bool:
    normalized_message = message.casefold()
    normalized_alias = str(alias).casefold()
    if re.fullmatch(r"[a-z0-9 -]+", normalized_alias):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
                normalized_message,
            )
        )
    return normalized_alias in normalized_message


def detect_rule_slot_operations(message: str) -> list[SlotOperation]:
    operations: list[SlotOperation] = []
    if not re.search(r"(?:不限制|不要|排除|去掉)[^，。；]*(?:季节|春|夏|秋|冬)", message):
        for season, terms in SEASON_TERMS.items():
            if any(term in message for term in terms):
                operations.append(
                    SlotOperation(field="season", operation="SET", value=season)
                )
                break
    for scope, terms in PRODUCT_SCOPE_TERMS.items():
        if any(term in message for term in terms):
            operations.append(
                SlotOperation(field="product_scope", operation="SET", value=scope)
            )
            has_specific_category = any(
                product_scope == scope
                and any(alias.casefold() in message.casefold() for alias in aliases)
                for _, product_scope, aliases in CONTEXTUAL_PRODUCT_OPTIONS
            )
            if not has_specific_category:
                operations.append(
                    SlotOperation(field="category", operation="CLEAR", value=None)
                )
            break
    for color_depth, terms in COLOR_DEPTH_TERMS.items():
        if any(term in message for term in terms):
            operations.append(
                SlotOperation(
                    field="color_depth",
                    operation="SET",
                    value=color_depth,
                )
            )
            if not any(
                term in message
                for color_terms in COLOR_TERMS.values()
                for term in color_terms
            ):
                operations.extend(
                    [
                        SlotOperation(field="color", operation="CLEAR", value=None),
                        SlotOperation(
                            field="negative_colors",
                            operation="CLEAR",
                            value=None,
                        ),
                    ]
                )
            break
    scenes = [
        scene
        for scene, rule in load_scene_rules().items()
        if any(message_contains_scene_alias(message, alias) for alias in rule["aliases"])
    ]
    if scenes:
        scene_operation = "ADD" if SCENE_ADDITION_PATTERN.search(message) else "SET"
        operations.append(
            SlotOperation(
                field="occasions",
                operation=scene_operation,
                value=scenes,
            )
        )
    for style, terms in STYLE_TERMS.items():
        if any(term in message for term in terms):
            operations.append(
                SlotOperation(
                    field="style_preferences",
                    operation="ADD",
                    value=[style],
                )
            )
    for body_goal, terms in BODY_GOAL_TERMS.items():
        if any(term in message for term in terms):
            operations.append(
                SlotOperation(
                    field="body_goals",
                    operation="ADD",
                    value=[body_goal],
                )
            )
    return operations


def apply_slot_operations(
    current_filters: NormalizedFilters | None,
    operations: list[SlotOperation],
) -> NormalizedFilters:
    defaults = NormalizedFilters().model_dump()
    values = current_filters.model_dump() if current_filters else defaults.copy()
    for operation in operations:
        field = operation.field
        action = operation.operation
        if action == SlotOperationType.KEEP:
            continue
        if action == SlotOperationType.CLEAR:
            values[field] = defaults[field]
            continue
        if action == SlotOperationType.SET:
            values[field] = operation.value
            continue
        if field not in LIST_FIELDS:
            raise ValueError(f"{action.value} is only valid for list slot {field}")
        if not isinstance(operation.value, list):
            raise ValueError(f"{action.value} requires a list value for {field}")
        current = list(values[field])
        if action == SlotOperationType.ADD:
            values[field] = list(dict.fromkeys([*current, *operation.value]))
        elif action == SlotOperationType.REMOVE:
            removals = set(operation.value)
            values[field] = [value for value in current if value not in removals]
    return NormalizedFilters.model_validate(values)


def merge_slot_operations(
    *operation_groups: list[SlotOperation],
) -> list[SlotOperation]:
    merged: list[SlotOperation] = []
    seen: set[tuple[str, str, str]] = set()
    for operation in (item for group in operation_groups for item in group):
        key = (
            operation.field,
            operation.operation.value,
            json.dumps(operation.value, ensure_ascii=False, sort_keys=True),
        )
        if key not in seen:
            seen.add(key)
            merged.append(operation)
    return merged


def parse_llm_slot_operations(items: list[dict[str, Any]]) -> list[SlotOperation]:
    operations: list[SlotOperation] = []
    for item in items:
        item = dict(item)
        if (
            item.get("field") == "category"
            and item.get("operation") == SlotOperationType.SET.value
        ):
            raw_value = str(item.get("value") or "").strip().casefold()
            aggregate_scope = AGGREGATE_CATEGORY_SCOPES.get(raw_value)
            if aggregate_scope is not None:
                operations.extend(
                    [
                        SlotOperation(
                            field="product_scope",
                            operation="SET",
                            value=aggregate_scope,
                        ),
                        SlotOperation(
                            field="category",
                            operation="CLEAR",
                            value=None,
                        ),
                    ]
                )
                continue
            if raw_value not in VALID_PRODUCT_CATEGORIES:
                raise ValueError(f"unsupported product category: {raw_value}")
            item["value"] = raw_value
        field = str(item.get("field") or "")
        action = item.get("operation")
        if action in {
            SlotOperationType.SET.value,
            SlotOperationType.ADD.value,
            SlotOperationType.REMOVE.value,
        }:
            if field in SCALAR_ENUM_VALUES:
                value = str(item.get("value") or "").strip().casefold()
                if value not in SCALAR_ENUM_VALUES[field]:
                    raise ValueError(f"unsupported {field}: {value}")
                item["value"] = value
            elif field in LIST_ENUM_VALUES:
                raw_values = item.get("value")
                if not isinstance(raw_values, list):
                    raise TypeError(f"{field} operation value must be a list")
                aliases = LLM_ENUM_ALIASES.get(field, {})
                values = [
                    aliases.get(str(value).strip().casefold(), str(value).strip().casefold())
                    for value in raw_values
                ]
                unsupported = [
                    value for value in values if value not in LIST_ENUM_VALUES[field]
                ]
                if field == "occasions":
                    values = [
                        value for value in values if value in LIST_ENUM_VALUES[field]
                    ]
                    if not values:
                        continue
                elif unsupported:
                    raise ValueError(f"unsupported {field}: {unsupported}")
                item["value"] = list(dict.fromkeys(values))
        operations.append(SlotOperation.model_validate(item))
    return operations


def normalize_intent_response_lists(parsed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parsed)
    for field in INTENT_RESPONSE_LIST_FIELDS:
        value = normalized[field]
        if value is None:
            normalized[field] = []
        elif not isinstance(value, list):
            raise TypeError(f"intent response field {field} must be a list")
    return normalized


def resolve_product_references(
    message: str,
    visible_product_ids: list[str],
    current_product_id: str | None,
    selected_product_ids: list[str] | None = None,
) -> list[str]:
    references: list[str] = []
    for raw_rank in re.findall(
        r"第\s*(\d+|十一|十二|一|二|三|四|五|六|七|八|九|十)\s*件",
        message,
    ):
        rank = int(raw_rank) if raw_rank.isdigit() else CHINESE_RANKS[raw_rank]
        if 1 <= rank <= len(visible_product_ids):
            references.append(visible_product_ids[rank - 1])
    if re.search(r"(?:这件|当前这件|选中的商品)", message) and current_product_id:
        references.append(current_product_id)
    if re.search(r"(?:这几件|这些商品|选中的这些|刚才选的)", message):
        references.extend(selected_product_ids or [])
    return list(dict.fromkeys(references))


def resolve_contextual_follow_up_intents(
    message: str,
    recent_messages: list[dict[str, str]],
    current_product_id: str | None,
) -> list[IntentType]:
    if current_product_id is None or not CONTEXTUAL_CONTINUATION_PATTERN.fullmatch(
        message.strip()
    ):
        return []
    last_assistant_message = next(
        (
            item["content"]
            for item in reversed(recent_messages)
            if item["role"] == "assistant"
        ),
        None,
    )
    if last_assistant_message is None or not re.search(
        r"(?:你想|想先|需要我|要不要|是否|还是)",
        last_assistant_message,
    ):
        return []

    question_clauses = re.findall(
        r"[^。！？!?]*[？?]",
        last_assistant_message,
    )
    question_context = (
        question_clauses[-1].strip()
        if question_clauses
        else last_assistant_message
    )
    offer_matches = list(
        re.finditer(
            r"(?:你想|想先|需要我|要不要|是否)",
            question_context,
        )
    )
    if offer_matches:
        question_context = question_context[offer_matches[-1].start() :]

    matches: list[tuple[int, IntentType]] = []
    for intent, pattern in CONTEXTUAL_TOPIC_PATTERNS:
        match = pattern.search(question_context)
        if match is not None:
            matches.append((match.start(), intent))
    if not matches and re.search(r"(?:细节|详细介绍|具体介绍)", question_context):
        return [IntentType.EXPLAIN_PRODUCT]
    return list(dict.fromkeys(intent for _, intent in sorted(matches)))


def offered_product_options(
    assistant_message: str,
) -> list[tuple[str, str]]:
    connector = re.search(r"(?:还是|或者|或是|、|/)", assistant_message)
    if connector is None:
        return []
    matches: list[tuple[int, int, str, str]] = []
    for category, product_scope, aliases in CONTEXTUAL_PRODUCT_OPTIONS:
        for alias in aliases:
            for match in re.finditer(re.escape(alias), assistant_message):
                matches.append(
                    (match.start(), match.end(), category, product_scope)
                )
    before = [match for match in matches if match[1] <= connector.start()]
    after = [match for match in matches if match[0] >= connector.end()]
    if not before or not after:
        return []
    nearest_before = max(before, key=lambda match: match[1])
    nearest_after = min(after, key=lambda match: match[0])
    return list(
        dict.fromkeys(
            [
                (nearest_before[2], nearest_before[3]),
                (nearest_after[2], nearest_after[3]),
            ]
        )
    )


def resolve_contextual_product_option(
    message: str,
    recent_messages: list[dict[str, str]],
) -> tuple[str, str] | None:
    normalized_message = re.sub(r"[\s，。！？!?、～~]+", "", message)
    selected_option = next(
        (
            (category, product_scope, aliases)
            for category, product_scope, aliases in CONTEXTUAL_PRODUCT_OPTIONS
            if normalized_message in aliases
        ),
        None,
    )
    if selected_option is None:
        return None

    last_assistant_message = next(
        (
            item["content"]
            for item in reversed(recent_messages)
            if item["role"] == "assistant"
        ),
        None,
    )
    if last_assistant_message is None:
        return None
    category, product_scope, _ = selected_option
    if (category, product_scope) not in offered_product_options(last_assistant_message):
        return None
    return category, product_scope


def resolve_contextual_comparison_preference(
    message: str,
    recent_messages: list[dict[str, str]],
    selected_product_ids: list[str],
) -> str | None:
    if not 2 <= len(selected_product_ids) <= 4:
        return None
    comparison_match: re.Match[str] | None = None
    for item in reversed(recent_messages):
        if item["role"] != "assistant":
            continue
        matches = list(COMPARISON_PREFERENCE_PATTERN.finditer(item["content"]))
        if matches:
            comparison_match = matches[-1]
            break
    if comparison_match is None:
        return None
    options = [comparison_match.group("first"), comparison_match.group("second")]
    comparison_message = comparison_match.string
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", comparison_message)
        if paragraph.strip()
    ]
    evidence = list(options)
    if len(selected_product_ids) == 2 and len(paragraphs) >= 3:
        evidence[0] += " " + paragraphs[1]
        evidence[1] += " " + paragraphs[2]

    def normalized(value: str) -> str:
        return re.sub(r"[\s，。！？!?、～~]+", "", value)

    normalized_message = normalized(message)
    if normalized_message in {"前者", "第一种", "第一个"}:
        return selected_product_ids[0]
    if normalized_message in {"后者", "第二种", "第二个"}:
        return selected_product_ids[-1]
    direct_rank_match = re.fullmatch(
        r"第?\s*(\d+|十一|十二|一|二|三|四|五|六|七|八|九|十)\s*件",
        message.strip(),
    )
    if direct_rank_match is not None:
        raw_rank = direct_rank_match.group(1)
        rank = int(raw_rank) if raw_rank.isdigit() else CHINESE_RANKS[raw_rank]
        if 1 <= rank <= len(selected_product_ids):
            return selected_product_ids[rank - 1]

    option_product_ids: list[str | None] = []
    for index, option in enumerate(options):
        rank_match = re.search(
            r"第?\s*(\d+|十一|十二|一|二|三|四|五|六|七|八|九|十)\s*件",
            option,
        )
        if rank_match is not None:
            raw_rank = rank_match.group(1)
            rank = int(raw_rank) if raw_rank.isdigit() else CHINESE_RANKS[raw_rank]
            option_product_ids.append(
                selected_product_ids[rank - 1]
                if 1 <= rank <= len(selected_product_ids)
                else None
            )
        elif len(selected_product_ids) == 2:
            option_product_ids.append(selected_product_ids[index])
        else:
            option_product_ids.append(None)

    for product_id, option in zip(option_product_ids, options):
        normalized_option = normalized(option)
        if product_id is not None and len(normalized_message) >= 2 and (
            normalized_message in normalized_option
            or normalized_option in normalized_message
        ):
            return product_id
    message_keywords = {
        keyword
        for keyword in COMPARISON_PREFERENCE_KEYWORDS
        if keyword in normalized_message
    }
    option_scores = [
        sum(keyword in normalized(option_evidence) for keyword in message_keywords)
        for option_evidence in evidence
    ]
    if (
        option_scores
        and max(option_scores) > 0
        and option_scores.count(max(option_scores)) == 1
    ):
        return option_product_ids[option_scores.index(max(option_scores))]
    return None


def resolve_current_product_recommendation(
    message: str,
    current_product_id: str | None,
) -> str | None:
    if current_product_id is None:
        return None
    if CURRENT_PRODUCT_RECOMMENDATION_PATTERN.fullmatch(message.strip()) is None:
        return None
    return current_product_id


def resolve_contextual_recommendation_confirmation(
    message: str,
    recent_messages: list[dict[str, str]],
) -> str | None:
    if not CONTEXTUAL_RECOMMENDATION_CONFIRMATION_PATTERN.fullmatch(message.strip()):
        return None
    last_assistant_message = next(
        (
            item["content"]
            for item in reversed(recent_messages)
            if item["role"] == "assistant"
        ),
        None,
    )
    if last_assistant_message is None:
        return None
    if not CONTEXTUAL_RECOMMENDATION_ACTION_PATTERN.search(last_assistant_message):
        return None
    if not CONTEXTUAL_RECOMMENDATION_OFFER_PATTERN.search(last_assistant_message):
        return None
    return last_assistant_message


def contextual_product_switch_operations(
    category: str,
    product_scope: str,
    current_filters: NormalizedFilters | None,
) -> list[SlotOperation]:
    operations = [
        SlotOperation(field="category", operation="SET", value=category),
        SlotOperation(
            field="product_scope",
            operation="SET",
            value=product_scope,
        ),
    ]
    if current_filters is None:
        return operations

    defaults = NormalizedFilters()
    operations.extend(
        SlotOperation(field=field, operation="CLEAR", value=None)
        for field in PRODUCT_SWITCH_CLEAR_FIELDS
        if getattr(current_filters, field) != getattr(defaults, field)
    )
    return operations


def infer_pending_action(
    assistant_message: str,
    filters: NormalizedFilters,
    current_product_id: str | None = None,
) -> PendingAction | None:
    offered_options = offered_product_options(assistant_message)
    if len(offered_options) >= 2 and re.search(
        r"(?:你想|想先|要不要|需要我|是否|看看|看搭配)",
        assistant_message,
    ):
        options = [
            PendingProductOption(category=category, product_scope=product_scope)
            for category, product_scope in offered_options
        ]
        return PendingAction(
            action=PendingActionType.SELECT_PRODUCT_CATEGORY,
            filters=filters,
            source_assistant_message=assistant_message,
            options=options,
        )
    if (
        CONTEXTUAL_RECOMMENDATION_ACTION_PATTERN.search(assistant_message)
        and CONTEXTUAL_RECOMMENDATION_OFFER_PATTERN.search(assistant_message)
    ):
        pending_filters = filters
        if (
            current_product_id is not None
            and CONTEXTUAL_ALTERNATIVE_RECOMMENDATION_PATTERN.search(
                assistant_message
            )
        ):
            pending_filters = filters.model_copy(
                update={
                    "excluded_product_ids": list(
                        dict.fromkeys(
                            [*filters.excluded_product_ids, current_product_id]
                        )
                    )
                }
            )
        return PendingAction(
            action=PendingActionType.RECOMMEND_PRODUCT,
            filters=pending_filters,
            source_assistant_message=assistant_message,
        )
    return None


def resolve_pending_action(
    message: str,
    pending_action: PendingAction | None,
    limit: int,
) -> ParsedShoppingRequest | None:
    if pending_action is None:
        return None
    if pending_action.action == PendingActionType.RECOMMEND_PRODUCT:
        if not CONTEXTUAL_RECOMMENDATION_CONFIRMATION_PATTERN.fullmatch(message.strip()):
            return None
        filters = pending_action.filters
        intent_result = IntentResult(
            intent=IntentType.RECOMMEND_PRODUCT,
            slots=filters,
            confidence=1.0,
            parser_source="RULE",
        )
        return ParsedShoppingRequest(
            intent_result=intent_result,
            recommendation_request=RecommendationRequest(
                raw_query=pending_action.source_assistant_message,
                **filters.model_dump(),
                only_in_stock=True,
                limit=limit,
            ),
        )

    normalized_message = re.sub(r"[\s，。！？!?、～~]+", "", message)
    selected_option = next(
        (
            (category, product_scope)
            for category, product_scope, aliases in CONTEXTUAL_PRODUCT_OPTIONS
            if normalized_message in aliases
        ),
        None,
    )
    allowed_options = {
        (option.category, option.product_scope) for option in pending_action.options
    }
    if selected_option is None or selected_option not in allowed_options:
        return None
    operations = contextual_product_switch_operations(
        *selected_option,
        pending_action.filters,
    )
    filters = apply_slot_operations(pending_action.filters, operations)
    intent_result = IntentResult(
        intent=IntentType.RECOMMEND_PRODUCT,
        slots=filters,
        slot_operations=operations,
        confidence=1.0,
        parser_source="RULE",
    )
    return ParsedShoppingRequest(
        intent_result=intent_result,
        recommendation_request=RecommendationRequest(
            raw_query=message,
            **filters.model_dump(),
            only_in_stock=True,
            limit=limit,
        ),
    )


def detect_price_currency(
    message: str,
    parsed_currency: CurrencyCode | None,
    current_currency: CurrencyCode,
) -> CurrencyCode:
    if re.search(r"(?:人民币|元|[¥￥])", message, re.IGNORECASE):
        return "CNY"
    if re.search(r"(?:美元|美金|USD|\$)", message, re.IGNORECASE):
        return "USD"
    return parsed_currency or current_currency


def detect_conflicts(filters: NormalizedFilters) -> list[str]:
    conflicts: list[str] = []
    if (
        filters.min_price is not None
        and filters.max_price is not None
        and filters.min_price > filters.max_price
    ):
        conflicts.append("最低价格高于最高价格")
    if filters.category is not None and filters.category in filters.excluded_categories:
        conflicts.append(f"同时要求并排除了品类 {filters.category}")
    if filters.color is not None and filters.color in filters.excluded_colors:
        conflicts.append(f"同时要求并排除了颜色 {filters.color}")
    if filters.material is not None and filters.material in filters.excluded_materials:
        conflicts.append(f"同时要求并排除了材质 {filters.material}")
    if filters.brand is not None and filters.brand.casefold() in {
        brand.casefold() for brand in filters.excluded_brands
    }:
        conflicts.append(f"同时要求并排除了品牌 {filters.brand}")
    if set(filters.style_preferences) & set(filters.excluded_styles):
        conflicts.append("同时要求并排除了相同风格")
    return conflicts


def detect_text_conflicts(message: str) -> list[str]:
    conflicts: list[str] = []
    positive_prefix = r"(?:想要|需要|必须(?:是)?|就要|选择)"
    negative_prefix = r"(?:不要|不想要|排除|不能(?:是)?|别选)"
    for normalized, terms in COLOR_TERMS.items():
        term_pattern = "(?:" + "|".join(map(re.escape, terms)) + ")"
        positive = re.search(positive_prefix + r"[^，。；]*" + term_pattern, message, re.IGNORECASE)
        negative = re.search(negative_prefix + r"[^，。；]*" + term_pattern, message, re.IGNORECASE)
        if positive and negative:
            conflicts.append(f"同时要求并排除了颜色 {normalized}")
    return conflicts


def clarification_question(
    intent: IntentType,
    conflicts: list[str],
    llm_question: str | None,
    product_references: list[str],
) -> str | None:
    if conflicts:
        return f"你的条件存在冲突：{'；'.join(conflicts)}。请确认要保留哪个条件？"
    if intent == IntentType.COMPARE_PRODUCTS and len(product_references) < 2:
        return "请告诉我要比较哪两件商品，例如“第一件和第三件”。"
    if intent in CONTEXT_REQUIRED_INTENTS and not product_references:
        return "请告诉我你想询问的具体商品，例如“第二件”或先点击商品卡片。"
    if intent == IntentType.UNKNOWN:
        return "请告诉我你想找什么服装，或想询问哪件商品的什么信息。"
    return llm_question


def read_project_env() -> dict[str, str]:
    return dict(
        line.split("=", maxsplit=1)
        for line in DEEPSEEK_ENV_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def read_deepseek_api_key() -> str:
    entries = read_project_env()
    return entries["DEEPSEEK_API_KEY"]


def read_intent_api_settings() -> IntentApiSettings:
    entries = read_project_env()
    scoped_values = {
        "base_url": os.getenv("STYLEMATE_INTENT_BASE_URL")
        or entries.get("STYLEMATE_INTENT_BASE_URL"),
        "api_key": os.getenv("STYLEMATE_INTENT_API_KEY")
        or entries.get("STYLEMATE_INTENT_API_KEY"),
        "model": os.getenv("STYLEMATE_INTENT_MODEL")
        or entries.get("STYLEMATE_INTENT_MODEL"),
    }
    missing = [key for key, value in scoped_values.items() if not value]
    if missing:
        raise ValueError(
            "StyleMate intent API configuration is incomplete: " + ", ".join(missing)
        )
    raw_model = str(scoped_values["model"])
    model = raw_model.split(":", maxsplit=1)[1] if ":" in raw_model else raw_model
    return IntentApiSettings(
        api_url=str(scoped_values["base_url"]).rstrip("/") + "/chat/completions",
        api_key=str(scoped_values["api_key"]),
        model=model,
        disable_thinking=True,
    )


def parse_shopping_request(
    message: str,
    limit: int,
    current_filters: NormalizedFilters | None = None,
    current_product_id: str | None = None,
    visible_product_ids: list[str] | None = None,
    recent_messages: list[dict[str, str]] | None = None,
    remembered_preferences: dict[str, str | float | list[str]] | None = None,
    selected_product_ids: list[str] | None = None,
    pending_action: PendingAction | None = None,
) -> ParsedShoppingRequest:
    resolved_pending_action = resolve_pending_action(message, pending_action, limit)
    if resolved_pending_action is not None:
        return resolved_pending_action

    app_intents = [
        intent for intent in detect_rule_intents(message) if intent in APP_ACTION_INTENTS
    ]
    if app_intents:
        product_references = resolve_product_references(
            message,
            visible_product_ids or [],
            current_product_id,
            selected_product_ids,
        )
        if (
            not product_references
            and current_product_id is not None
            and app_intents[0]
            in {
                IntentType.START_TRY_ON,
                IntentType.OPEN_PRODUCT_DETAIL,
                IntentType.PURCHASE_PRODUCT,
                IntentType.CONTACT_SALES,
            }
        ):
            product_references = [current_product_id]
        return ParsedShoppingRequest(
            intent_result=IntentResult(
                intent=app_intents[0],
                secondary_intents=app_intents[1:],
                slots=current_filters or NormalizedFilters(),
                product_references=product_references,
                confidence=1.0,
                parser_source="RULE",
            ),
            recommendation_request=None,
        )

    recommended_product_id = resolve_current_product_recommendation(
        message,
        current_product_id,
    )
    if recommended_product_id is not None:
        return ParsedShoppingRequest(
            intent_result=IntentResult(
                intent=IntentType.EXPLAIN_PRODUCT,
                slots=current_filters or NormalizedFilters(),
                product_references=[recommended_product_id],
                confidence=1.0,
                parser_source="RULE",
            ),
            recommendation_request=None,
        )

    comparison_product_id = resolve_contextual_comparison_preference(
        message,
        recent_messages or [],
        selected_product_ids or [],
    )
    if comparison_product_id is not None:
        return ParsedShoppingRequest(
            intent_result=IntentResult(
                intent=IntentType.VIEW_PRODUCT,
                slots=current_filters or NormalizedFilters(),
                product_references=[comparison_product_id],
                confidence=1.0,
                parser_source="RULE",
            ),
            recommendation_request=None,
        )

    contextual_product_option = resolve_contextual_product_option(
        message,
        recent_messages or [],
    )
    if contextual_product_option is not None:
        operations = contextual_product_switch_operations(
            *contextual_product_option,
            current_filters,
        )
        filters = apply_slot_operations(current_filters, operations)
        intent_result = IntentResult(
            intent=IntentType.RECOMMEND_PRODUCT,
            slots=filters,
            slot_operations=operations,
            confidence=1.0,
            parser_source="RULE",
        )
        return ParsedShoppingRequest(
            intent_result=intent_result,
            recommendation_request=RecommendationRequest(
                raw_query=message,
                **filters.model_dump(),
                only_in_stock=True,
                limit=limit,
            ),
        )

    contextual_recommendation = resolve_contextual_recommendation_confirmation(
        message,
        recent_messages or [],
    )
    if contextual_recommendation is not None:
        filters = current_filters or NormalizedFilters()
        if (
            current_product_id is not None
            and CONTEXTUAL_ALTERNATIVE_RECOMMENDATION_PATTERN.search(
                contextual_recommendation
            )
        ):
            filters = filters.model_copy(
                update={
                    "excluded_product_ids": list(
                        dict.fromkeys(
                            [*filters.excluded_product_ids, current_product_id]
                        )
                    )
                }
            )
        intent_result = IntentResult(
            intent=IntentType.RECOMMEND_PRODUCT,
            slots=filters,
            confidence=1.0,
            parser_source="RULE",
        )
        return ParsedShoppingRequest(
            intent_result=intent_result,
            recommendation_request=RecommendationRequest(
                raw_query=contextual_recommendation,
                **filters.model_dump(),
                only_in_stock=True,
                limit=limit,
            ),
        )

    contextual_intents = resolve_contextual_follow_up_intents(
        message,
        recent_messages or [],
        current_product_id,
    )
    if contextual_intents:
        return ParsedShoppingRequest(
            intent_result=IntentResult(
                intent=contextual_intents[0],
                secondary_intents=contextual_intents[1:],
                slots=current_filters or NormalizedFilters(),
                product_references=[current_product_id],
                confidence=1.0,
                parser_source="RULE",
            ),
            recommendation_request=None,
        )

    taxonomy = load_intent_taxonomy()
    intent_api = read_intent_api_settings()
    payload = {
        "model": intent_api.model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + "\n配置中的合法意图和槽位："
                + json.dumps(taxonomy, ensure_ascii=False),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_filters": (
                            current_filters.model_dump() if current_filters else None
                        ),
                        "current_product_id": current_product_id,
                        "selected_product_ids": selected_product_ids or [],
                        "visible_product_ids": visible_product_ids or [],
                        "recent_messages": recent_messages or [],
                        "remembered_preferences": remembered_preferences or {},
                        "new_message": message,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 900,
    }
    if intent_api.disable_thinking:
        payload["enable_thinking"] = False
    started = time.perf_counter()
    response = httpx.post(
        intent_api.api_url,
        headers={
            "Authorization": f"Bearer {intent_api.api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()
    response_data = response.json()
    elapsed_seconds = round(time.perf_counter() - started, 3)
    log_api_usage(
        request_index=time.time_ns(),
        call_type="intent_parse",
        call_type_index=1,
        model=intent_api.model,
        prefix="shopping_intent_parse",
        elapsed_seconds=elapsed_seconds,
        usage=response_data["usage"],
    )
    parsed = normalize_intent_response_lists(
        json.loads(response_data["choices"][0]["message"]["content"])
    )
    raw_llm_operations = parsed["slot_operations"]
    unsupported_occasions = unsupported_llm_occasion_values(raw_llm_operations)
    rule_operations = detect_rule_slot_operations(message)
    llm_requested_occasion = any(
        item.get("field") == "occasions"
        and item.get("operation")
        in {SlotOperationType.SET.value, SlotOperationType.ADD.value}
        for item in raw_llm_operations
    )
    rule_recognized_occasion = any(
        operation.field == "occasions" for operation in rule_operations
    )
    unsupported_occasion_signal = (
        bool(unsupported_occasions)
        or UNSUPPORTED_OCCASION_WARNING in parsed["warnings"]
        or (
            llm_requested_occasion
            and not rule_recognized_occasion
            and CLOTHING_USAGE_PATTERN.search(message) is not None
        )
    )
    llm_operations = parse_llm_slot_operations(raw_llm_operations)
    if unsupported_occasion_signal:
        llm_operations = [
            operation
            for operation in llm_operations
            if operation.field != "occasions"
        ]
    if any(operation.field == "color_depth" for operation in rule_operations) and not any(
        term in message
        for color_terms in COLOR_TERMS.values()
        for term in color_terms
    ):
        llm_operations = [
            operation
            for operation in llm_operations
            if operation.field not in {"color", "excluded_colors", "negative_colors"}
        ]
    operations = merge_slot_operations(
        llm_operations,
        rule_operations,
    )
    filters = apply_slot_operations(current_filters, operations)
    filters = filters.model_copy(
        update={
            "price_currency": detect_price_currency(
                message,
                filters.price_currency,
                current_filters.price_currency if current_filters else "USD",
            )
        }
    )

    rule_intents = detect_rule_intents(message)
    llm_primary = IntentType(parsed["primary_intent"])
    llm_secondary = [IntentType(value) for value in parsed["secondary_intents"]]
    intent = rule_intents[0] if rule_intents else llm_primary
    secondary_intents = list(
        dict.fromkeys(
            value
            for value in [*rule_intents[1:], llm_primary, *llm_secondary]
            if value != intent
        )
    )
    unsupported_occasion_requested = (
        intent in {*RECOMMENDATION_INTENTS, IntentType.OUTFIT_ADVICE}
        and unsupported_occasion_signal
    )
    parser_source = (
        "RULE_LLM_FUSION"
        if rule_intents or len(operations) != len(llm_operations)
        else "LLM"
    )
    confidence = float(parsed["confidence"])
    if rule_intents or len(operations) != len(llm_operations):
        confidence = max(confidence, 0.95)

    product_references = resolve_product_references(
        message,
        visible_product_ids or [],
        current_product_id,
        selected_product_ids,
    )
    if intent in CONTEXT_REQUIRED_INTENTS and not product_references:
        if intent == IntentType.COMPARE_PRODUCTS and selected_product_ids:
            product_references = selected_product_ids
        elif current_product_id:
            product_references = [current_product_id]
    if (
        intent == IntentType.REQUEST_ALTERNATIVE
        and current_product_id is not None
        and current_product_id not in filters.excluded_product_ids
    ):
        filters = filters.model_copy(
            update={
                "excluded_product_ids": [
                    *filters.excluded_product_ids,
                    current_product_id,
                ]
            }
        )
    if intent in {IntentType.CLEAR_FILTERS, IntentType.RESET_SESSION}:
        filters = NormalizedFilters()

    conflicts = list(
        dict.fromkeys([*detect_conflicts(filters), *detect_text_conflicts(message)])
    )
    question = (
        UNSUPPORTED_OCCASION_MESSAGE
        if unsupported_occasion_requested
        else clarification_question(
            intent,
            conflicts,
            parsed["clarify_question"],
            product_references,
        )
    )
    clarify_needed = bool(parsed["clarify_needed"] or question or conflicts)
    if intent == IntentType.OUT_OF_SCOPE:
        fulfillment_status = "OUT_OF_SCOPE"
    elif intent in {
        IntentType.ASK_TREND,
        IntentType.SEARCH_EXTERNAL_PRODUCT,
        IntentType.ASK_NEW_ARRIVAL,
    }:
        fulfillment_status = "UNSUPPORTED_DATA"
    elif clarify_needed:
        fulfillment_status = "NEED_CLARIFICATION"
    elif intent == IntentType.ASK_CARE:
        fulfillment_status = "UNSUPPORTED_DATA"
    else:
        fulfillment_status = "READY"
    warnings = list(parsed["warnings"])
    if unsupported_occasion_requested:
        warnings = list(dict.fromkeys([*warnings, UNSUPPORTED_OCCASION_WARNING]))
    intent_result = IntentResult(
        intent=intent,
        secondary_intents=secondary_intents,
        slots=filters,
        slot_operations=operations,
        requested_fields=list(parsed["requested_fields"]),
        product_references=product_references,
        confidence=confidence,
        parser_source=parser_source,
        clarify_needed=clarify_needed,
        clarify_question=question,
        conflicts=conflicts,
        warnings=warnings,
        fulfillment_status=fulfillment_status,
    )
    recommendation_request = None
    if not clarify_needed and intent in RECOMMENDATION_INTENTS:
        recommendation_request = RecommendationRequest(
            raw_query=message,
            **filters.model_dump(),
            only_in_stock=True,
            limit=limit,
        )
    return ParsedShoppingRequest(
        intent_result=intent_result,
        recommendation_request=recommendation_request,
    )
