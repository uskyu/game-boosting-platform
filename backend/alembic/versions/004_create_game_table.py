"""create games table

Revision ID: 004_create_game_table
Revises: 003_add_chat_tables
Create Date: 2026-03-31_00_00_00
"""

from typing import Any, Sequence, Union
from urllib.parse import quote_plus

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004_create_game_table"
down_revision: Union[str, None] = "003_add_chat_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def build_service_template(
    service_types: list[str],
    has_rank_system: bool,
    *,
    rank_tiers: list[str] | None = None,
    servers: list[str] | None = None,
    roles: list[str] | None = None,
    custom_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "service_types": service_types,
        "has_rank_system": has_rank_system,
        "rank_tiers": rank_tiers or [],
        "servers": servers or [],
        "roles": roles or [],
        "custom_fields": custom_fields or [],
    }


def build_image_url(width: int, height: int, color_theme: str, text: str) -> str:
    color = color_theme.removeprefix("#")
    return f"https://placehold.co/{width}x{height}/{color}/FFFFFF?text={quote_plus(text)}"


def build_game(
    game_id: int,
    name: str,
    english_name: str,
    category: str,
    platform: str,
    color_theme: str,
    service_template: dict[str, Any],
    description: str | None = None,
) -> dict[str, Any]:
    image_text = english_name or name
    return {
        "id": game_id,
        "name": name,
        "english_name": english_name,
        "category": category,
        "platform": platform,
        "icon_url": build_image_url(256, 256, color_theme, image_text),
        "cover_url": build_image_url(1200, 675, color_theme, image_text),
        "color_theme": color_theme,
        "service_template": service_template,
        "description": description or f"{name}热门服务专区",
        "is_active": True,
        "sort_order": game_id,
    }


GAME_SEEDS: list[dict[str, Any]] = [
    # MOBA
    build_game(
        1,
        "王者荣耀",
        "Honor of Kings",
        "MOBA",
        "MOBILE",
        "#E58E26",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "星耀", "王者"],
            servers=["微信区", "QQ区"],
            roles=["对抗路", "打野", "中单", "发育路", "游走"],
        ),
        "国民级 5v5 MOBA 热门服务专区",
    ),
    build_game(
        2,
        "英雄联盟",
        "League of Legends",
        "MOBA",
        "PC",
        "#1E3A8A",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["黑铁", "青铜", "白银", "黄金", "铂金", "翡翠", "钻石", "大师", "宗师", "王者"],
            servers=["艾欧尼亚", "祖安", "诺克萨斯", "比尔吉沃特", "黑色玫瑰"],
            roles=["上单", "打野", "中单", "ADC", "辅助"],
        ),
    ),
    build_game(
        3,
        "英雄联盟手游",
        "League of Legends Wild Rift",
        "MOBA",
        "MOBILE",
        "#0EA5E9",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["黑铁", "青铜", "白银", "黄金", "铂金", "翡翠", "钻石", "大师", "宗师", "王者"],
            servers=["微信区", "QQ区"],
            roles=["上单", "打野", "中单", "双人路", "辅助"],
        ),
    ),
    build_game(
        4,
        "DOTA2",
        "Dota 2",
        "MOBA",
        "PC",
        "#B91C1C",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["先锋", "卫士", "中军", "统帅", "传奇", "万古流芳", "超凡入圣", "冠绝一世"],
            servers=["国服", "东南亚", "欧洲西部"],
            roles=["1号位", "2号位", "3号位", "4号位", "5号位"],
        ),
    ),
    build_game(
        5,
        "曙光英雄",
        "Heroes of Dawn",
        "MOBA",
        "MOBILE",
        "#F97316",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "星耀", "王者"],
            servers=["微信区", "QQ区"],
            roles=["对抗路", "打野", "中路", "射手", "辅助"],
        ),
    ),
    build_game(
        6,
        "决战！平安京",
        "Onmyoji Arena",
        "MOBA",
        "BOTH",
        "#7C3AED",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["见习", "得业生", "阴阳少属", "阴阳大属", "阴阳少允", "阴阳大允", "大阴阳师", "名士"],
            servers=["网易官服", "渠道服"],
            roles=["上路", "打野", "中路", "射手", "辅助"],
        ),
    ),
    # FPS
    build_game(
        7,
        "和平精英",
        "Game for Peace",
        "FPS",
        "MOBILE",
        "#22C55E",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["热血青铜", "不屈白银", "英勇黄金", "坚韧铂金", "不朽星钻", "荣耀皇冠", "超级王牌", "无敌战神"],
            servers=["微信区", "QQ区"],
            roles=["突击手", "狙击手", "侦察位", "自由人", "指挥"],
        ),
    ),
    build_game(
        8,
        "CS2",
        "Counter-Strike 2",
        "FPS",
        "PC",
        "#2563EB",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["白银", "黄金新星", "AK", "双AK", "徽章", "老鹰", "大地球", "全球精英"],
            servers=["国服(完美)", "国际服"],
            roles=["突破手", "狙击手", "自由人", "指挥", "辅助"],
        ),
    ),
    build_game(
        9,
        "穿越火线",
        "CrossFire",
        "FPS",
        "PC",
        "#DC2626",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["列兵", "三等兵", "二等兵", "一等兵", "下士", "中士", "上士", "少尉", "中尉", "上尉", "少校", "中校", "上校", "大校", "少将", "中将", "上将", "元帅"],
            servers=["北部战区", "东部战区", "南部战区", "西部战区"],
            roles=["狙击", "步枪", "突破", "自由人"],
        ),
    ),
    build_game(
        10,
        "穿越火线手游",
        "CrossFire Legends",
        "FPS",
        "MOBILE",
        "#EF4444",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["新锐", "精英", "专家", "大师", "枪王", "枪神", "荣耀枪神"],
            servers=["微信区", "QQ区"],
            roles=["狙击", "步枪", "突破", "自由人"],
        ),
    ),
    build_game(
        11,
        "三角洲行动",
        "Delta Force",
        "FPS",
        "BOTH",
        "#10B981",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "大师", "宗师"],
            servers=["微信区", "QQ区", "PC官服"],
            roles=["突击", "侦察", "工程", "支援"],
        ),
    ),
    build_game(
        12,
        "无畏契约 (VALORANT)",
        "VALORANT",
        "FPS",
        "PC",
        "#F43F5E",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["黑铁", "青铜", "白银", "黄金", "铂金", "钻石", "超凡", "神话", "辐能战魂"],
            servers=["亚太服", "国服"],
            roles=["决斗", "先锋", "控场", "哨卫"],
        ),
    ),
    build_game(
        13,
        "暗区突围",
        "Arena Breakout",
        "FPS",
        "MOBILE",
        "#64748B",
        build_service_template(
            ["代练通关", "陪玩"],
            True,
            rank_tiers=["新锐", "尖兵", "精英", "专家", "大师", "王牌", "传说"],
            servers=["微信区", "QQ区"],
            roles=["突击", "狙击", "后勤"],
            custom_fields=["地图", "撤离要求"],
        ),
    ),
    # RPG
    build_game(
        14,
        "原神",
        "Genshin Impact",
        "RPG",
        "BOTH",
        "#38BDF8",
        build_service_template(
            ["代刷材料", "代打深渊", "代做任务", "陪玩"],
            False,
            servers=["官服", "B服"],
            roles=["主C", "副C", "辅助", "奶妈"],
            custom_fields=["冒险等级", "世界等级"],
        ),
    ),
    build_game(
        15,
        "崩坏：星穹铁道",
        "Honkai Star Rail",
        "RPG",
        "BOTH",
        "#60A5FA",
        build_service_template(
            ["代刷材料", "代打关卡", "代做任务"],
            False,
            servers=["官服", "B服"],
            roles=["巡猎", "毁灭", "同谐", "丰饶"],
            custom_fields=["开拓等级", "均衡等级"],
        ),
    ),
    build_game(
        16,
        "绝区零",
        "Zenless Zone Zero",
        "RPG",
        "BOTH",
        "#F59E0B",
        build_service_template(
            ["代刷材料", "代打关卡", "陪玩"],
            False,
            servers=["官服", "B服"],
            roles=["强攻", "击破", "异常", "支援", "防护"],
            custom_fields=["绳网等级", "代理人等级"],
        ),
    ),
    build_game(
        17,
        "鸣潮",
        "Wuthering Waves",
        "RPG",
        "BOTH",
        "#0F766E",
        build_service_template(
            ["代刷材料", "代打关卡", "陪玩"],
            False,
            servers=["官服", "B服"],
            roles=["主C", "副C", "治疗", "增伤"],
            custom_fields=["联觉等级", "世界等级"],
        ),
    ),
    build_game(
        18,
        "梦幻西游",
        "Fantasy Westward Journey",
        "RPG",
        "BOTH",
        "#FBBF24",
        build_service_template(
            ["代练等级", "代刷副本", "跑环", "陪玩"],
            False,
            servers=["安卓区", "iOS区", "互通区", "电脑版"],
            roles=["大唐官府", "龙宫", "方寸山", "普陀山", "化生寺"],
            custom_fields=["角色等级", "服务器", "门派"],
        ),
    ),
    build_game(
        19,
        "逆水寒",
        "Justice",
        "RPG",
        "BOTH",
        "#84CC16",
        build_service_template(
            ["代练等级", "代刷副本", "陪玩"],
            False,
            servers=["官服", "渠道服", "PC服"],
            roles=["神相", "碎梦", "铁衣", "素问", "血河"],
            custom_fields=["角色等级", "装等", "流派"],
        ),
    ),
    build_game(
        20,
        "燕云十六声",
        "Where Winds Meet",
        "RPG",
        "PC",
        "#475569",
        build_service_template(
            ["代练等级", "代做任务", "陪玩"],
            False,
            servers=["国服"],
            roles=["陌刀", "双刀", "伞", "扇"],
            custom_fields=["角色等级", "心法进度"],
        ),
    ),
    # RACING
    build_game(
        21,
        "QQ飞车手游",
        "QQ Speed Mobile",
        "RACING",
        "MOBILE",
        "#06B6D4",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["新秀", "专业", "大师", "车神", "传奇车神"],
            servers=["微信区", "QQ区"],
            custom_fields=["驾照等级"],
        ),
    ),
    build_game(
        22,
        "跑跑卡丁车手游",
        "KartRider Rush Plus",
        "RACING",
        "MOBILE",
        "#F97316",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "大师", "车王"],
            servers=["微信区", "QQ区"],
            custom_fields=["驾照等级"],
        ),
    ),
    build_game(
        23,
        "极品飞车：集结",
        "Need for Speed Assemble",
        "RACING",
        "MOBILE",
        "#DC2626",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "传奇"],
            servers=["微信区", "QQ区"],
            custom_fields=["车辆评分"],
        ),
    ),
    build_game(
        24,
        "巅峰极速",
        "Racing Master",
        "RACING",
        "MOBILE",
        "#1D4ED8",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["新秀", "精英", "大师", "传奇"],
            servers=["网易官服", "渠道服"],
            custom_fields=["车库评分"],
        ),
    ),
    build_game(
        25,
        "王牌竞速",
        "Ace Racer",
        "RACING",
        "MOBILE",
        "#8B5CF6",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "大师", "车神"],
            servers=["网易官服", "渠道服"],
            roles=["竞速位", "辅助位", "干扰位"],
        ),
    ),
    # CARD
    build_game(
        26,
        "金铲铲之战",
        "Golden Spatula",
        "CARD",
        "MOBILE",
        "#F59E0B",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["黑铁", "青铜", "白银", "黄金", "铂金", "翡翠", "钻石", "大师", "宗师", "王者"],
            servers=["微信区", "QQ区"],
            custom_fields=["赛季段位"],
        ),
    ),
    build_game(
        27,
        "炉石传说",
        "Hearthstone",
        "CARD",
        "BOTH",
        "#C084FC",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "传说"],
            servers=["国服", "亚服", "美服"],
            custom_fields=["模式"],
        ),
    ),
    build_game(
        28,
        "阴阳师",
        "Onmyoji",
        "CARD",
        "BOTH",
        "#EF4444",
        build_service_template(
            ["代刷副本", "代肝活动", "陪玩"],
            False,
            servers=["网易官服", "渠道服"],
            custom_fields=["阴阳师等级", "式神练度"],
        ),
    ),
    build_game(
        29,
        "三国杀",
        "Three Kingdoms Kill",
        "CARD",
        "BOTH",
        "#A16207",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["校尉", "中郎将", "偏将军", "裨将军", "牙门将军", "镇军将军", "辅国将军", "骠骑将军", "大将军"],
            servers=["手Q区", "微信区", "网页版"],
            roles=["主公", "忠臣", "反贼", "内奸"],
        ),
    ),
    build_game(
        30,
        "游戏王：决斗链接",
        "Yu-Gi-Oh Duel Links",
        "CARD",
        "MOBILE",
        "#2563EB",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "白金", "传说", "决斗王"],
            servers=["国服"],
            custom_fields=["活动进度"],
        ),
    ),
    build_game(
        31,
        "龙息：神寂",
        "Dragonheir Silent Gods",
        "CARD",
        "MOBILE",
        "#7C3AED",
        build_service_template(
            ["代练上分", "代刷副本"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "大师"],
            servers=["国服"],
            custom_fields=["战队练度"],
        ),
    ),
    # SPORTS
    build_game(
        32,
        "FIFA Online 4",
        "FIFA Online 4",
        "SPORTS",
        "PC",
        "#22C55E",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "世界级", "传奇"],
            servers=["QQ区", "微信区"],
            roles=["前锋", "中场", "后卫", "门将"],
        ),
    ),
    build_game(
        33,
        "实况足球手游",
        "eFootball Mobile",
        "SPORTS",
        "MOBILE",
        "#16A34A",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["新秀", "职业", "顶级", "传奇"],
            servers=["官服", "渠道服"],
            roles=["前锋", "中场", "后卫", "门将"],
        ),
    ),
    build_game(
        34,
        "NBA2K Online 2",
        "NBA2K Online 2",
        "SPORTS",
        "PC",
        "#2563EB",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["新秀", "高手", "全明星", "名人堂", "传奇"],
            servers=["电信区", "网通区"],
            roles=["PG", "SG", "SF", "PF", "C"],
        ),
    ),
    build_game(
        35,
        "欢乐斗地主",
        "Happy Landlord",
        "SPORTS",
        "BOTH",
        "#F59E0B",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["新手", "初级", "中级", "高级", "大师", "王者"],
            servers=["微信区", "QQ区"],
            roles=["地主", "农民"],
        ),
    ),
    build_game(
        36,
        "欢乐麻将",
        "Happy Mahjong",
        "SPORTS",
        "MOBILE",
        "#14B8A6",
        build_service_template(
            ["陪玩"],
            False,
            servers=["微信区", "QQ区"],
            custom_fields=["玩法", "场次"],
        ),
    ),
    # STRATEGY
    build_game(
        37,
        "率土之滨",
        "Infinite Borders",
        "STRATEGY",
        "MOBILE",
        "#B45309",
        build_service_template(
            ["代练发展", "代管账号", "陪玩"],
            False,
            servers=["官服", "渠道服"],
            roles=["控号", "开荒", "打城"],
            custom_fields=["赛季", "势力值", "州府"],
        ),
    ),
    build_game(
        38,
        "三国志战略版",
        "Three Kingdoms Strategy Edition",
        "STRATEGY",
        "MOBILE",
        "#92400E",
        build_service_template(
            ["代练发展", "代管账号"],
            False,
            servers=["官服", "渠道服"],
            roles=["开荒", "铺路", "攻城"],
            custom_fields=["赛季", "势力值", "同盟"],
        ),
    ),
    build_game(
        39,
        "三国志・战棋版",
        "Three Kingdoms Tactics Board War",
        "STRATEGY",
        "MOBILE",
        "#7C2D12",
        build_service_template(
            ["代打关卡", "代练发展"],
            False,
            servers=["官服"],
            roles=["骑兵", "弓兵", "谋士", "盾兵"],
            custom_fields=["主线进度", "武将练度"],
        ),
    ),
    build_game(
        40,
        "文明与征服",
        "Civilization and Conquest",
        "STRATEGY",
        "MOBILE",
        "#1E40AF",
        build_service_template(
            ["代练发展", "代管账号"],
            False,
            servers=["官服", "渠道服"],
            roles=["开荒", "城建", "攻城"],
            custom_fields=["文明等级", "城建进度"],
        ),
    ),
    build_game(
        41,
        "万国觉醒",
        "Rise of Kingdoms",
        "STRATEGY",
        "MOBILE",
        "#CA8A04",
        build_service_template(
            ["代练发展", "代管账号"],
            False,
            servers=["国服", "国际服"],
            roles=["采集", "打野", "驻防", "集结"],
            custom_fields=["市政厅等级", "战力"],
        ),
    ),
    build_game(
        42,
        "重返帝国",
        "Return to Empire",
        "STRATEGY",
        "MOBILE",
        "#991B1B",
        build_service_template(
            ["代练发展", "代管账号"],
            False,
            servers=["微信区", "QQ区"],
            roles=["采集", "开荒", "攻城"],
            custom_fields=["开荒进度", "城建等级"],
        ),
    ),
    # FIGHTING
    build_game(
        43,
        "地下城与勇士",
        "Dungeon and Fighter",
        "FIGHTING",
        "PC",
        "#DC2626",
        build_service_template(
            ["代刷副本", "代练等级", "搬砖"],
            False,
            servers=["跨一", "跨二", "跨三", "跨六"],
            roles=["鬼剑士", "格斗家", "神枪手", "魔法师", "圣职者"],
            custom_fields=["角色等级", "名望值", "职业"],
        ),
    ),
    build_game(
        44,
        "地下城与勇士：起源",
        "Dungeon and Fighter Mobile",
        "FIGHTING",
        "MOBILE",
        "#F87171",
        build_service_template(
            ["代刷副本", "代练等级"],
            False,
            servers=["微信区", "QQ区"],
            roles=["鬼剑士", "格斗家", "神枪手", "魔法师"],
            custom_fields=["角色等级", "冒险团等级", "职业"],
        ),
    ),
    build_game(
        45,
        "拳皇命运",
        "The King of Fighters Destiny",
        "FIGHTING",
        "MOBILE",
        "#EF4444",
        build_service_template(
            ["代练上分", "代刷副本"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "白金", "钻石", "拳皇"],
            servers=["微信区", "QQ区"],
            roles=["攻击", "防御", "技巧"],
        ),
    ),
    build_game(
        46,
        "街霸：对决",
        "Street Fighter Duel",
        "FIGHTING",
        "MOBILE",
        "#E11D48",
        build_service_template(
            ["代练上分", "陪玩"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "钻石", "大师", "传奇"],
            servers=["官服", "渠道服"],
            roles=["输出", "坦克", "辅助"],
        ),
    ),
    build_game(
        47,
        "火影忍者手游",
        "Naruto Mobile",
        "FIGHTING",
        "MOBILE",
        "#F97316",
        build_service_template(
            ["代练上分", "代刷副本", "陪玩"],
            True,
            rank_tiers=["见习忍者", "下忍", "中忍", "上忍", "暗部", "影"],
            servers=["微信区", "QQ区"],
            roles=["强攻", "连招", "控制"],
        ),
    ),
    build_game(
        48,
        "鬼泣：巅峰之战",
        "Devil May Cry Peak of Combat",
        "FIGHTING",
        "MOBILE",
        "#7F1D1D",
        build_service_template(
            ["代打关卡", "代练等级"],
            False,
            servers=["官服", "渠道服"],
            roles=["近战", "远程"],
            custom_fields=["角色等级", "武器评分"],
        ),
    ),
    # SURVIVAL
    build_game(
        49,
        "蛋仔派对",
        "Eggy Party",
        "SURVIVAL",
        "MOBILE",
        "#FACC15",
        build_service_template(
            ["陪玩", "代练上分"],
            True,
            rank_tiers=["鸡蛋", "鹌鹑蛋", "鹅蛋", "鸵鸟蛋", "凤凰蛋"],
            servers=["网易官服", "渠道服"],
            roles=["竞速", "乐园", "巅峰派对"],
        ),
    ),
    build_game(
        50,
        "明日之后",
        "LifeAfter",
        "SURVIVAL",
        "BOTH",
        "#16A34A",
        build_service_template(
            ["代练等级", "代刷材料", "陪玩"],
            False,
            servers=["官服", "渠道服"],
            roles=["采集", "制作", "战斗"],
            custom_fields=["庄园等级", "采集等级", "服务器"],
        ),
    ),
    build_game(
        51,
        "永劫无间",
        "Naraka Bladepoint",
        "SURVIVAL",
        "BOTH",
        "#DC2626",
        build_service_template(
            ["代练上分", "陪玩", "教学"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "陨星", "蚀月", "坠日", "无间修罗"],
            servers=["国服", "Steam服"],
            roles=["单排", "三排", "振刀教学"],
        ),
    ),
    build_game(
        52,
        "香肠派对",
        "Sausage Man",
        "SURVIVAL",
        "MOBILE",
        "#F59E0B",
        build_service_template(
            ["陪玩", "代练上分"],
            True,
            rank_tiers=["青铜", "白银", "黄金", "铂金", "钻石", "大师", "巅峰"],
            servers=["微信区", "QQ区"],
            roles=["钢枪", "狙击", "娱乐"],
        ),
    ),
    build_game(
        53,
        "方舟：生存进化",
        "ARK Survival Evolved",
        "SURVIVAL",
        "BOTH",
        "#0F766E",
        build_service_template(
            ["代练等级", "代刷材料"],
            False,
            servers=["官方PVE", "官方PVP", "私服"],
            roles=["采集", "驯龙", "建家"],
            custom_fields=["角色等级", "服务器类型", "部落规模"],
        ),
    ),
    build_game(
        54,
        "黎明觉醒",
        "Dawn Awakening",
        "SURVIVAL",
        "MOBILE",
        "#10B981",
        build_service_template(
            ["代练等级", "代刷材料"],
            False,
            servers=["微信区", "QQ区"],
            roles=["采集", "建造", "战斗"],
            custom_fields=["角色等级", "庄园等级", "营地"],
        ),
    ),
    # RHYTHM
    build_game(
        55,
        "Phigros",
        "Phigros",
        "RHYTHM",
        "MOBILE",
        "#8B5CF6",
        build_service_template(
            ["代打谱面", "陪玩"],
            False,
            servers=["iOS", "Android"],
            custom_fields=["RKS等级"],
        ),
    ),
    build_game(
        56,
        "节奏大师",
        "Rhythm Master",
        "RHYTHM",
        "MOBILE",
        "#EC4899",
        build_service_template(
            ["代打关卡", "陪玩"],
            False,
            servers=["微信区", "QQ区"],
            custom_fields=["段位", "歌曲难度"],
        ),
    ),
    build_game(
        57,
        "世界计划缤纷舞台",
        "Hatsune Miku Colorful Stage",
        "RHYTHM",
        "MOBILE",
        "#14B8A6",
        build_service_template(
            ["代打活动", "代肝"],
            False,
            servers=["国服", "国际服"],
            roles=["多指", "单手", "活动冲榜"],
            custom_fields=["玩家等级", "活动进度"],
        ),
    ),
    build_game(
        58,
        "Arcaea",
        "Arcaea",
        "RHYTHM",
        "MOBILE",
        "#6366F1",
        build_service_template(
            ["代打谱面"],
            False,
            servers=["国际服"],
            custom_fields=["PTT等级"],
        ),
    ),
    build_game(
        59,
        "喵斯快跑",
        "Muse Dash",
        "RHYTHM",
        "MOBILE",
        "#FB7185",
        build_service_template(
            ["代打谱面", "陪玩"],
            False,
            servers=["iOS", "Android"],
            custom_fields=["等级", "收藏进度"],
        ),
    ),
]


assert len(GAME_SEEDS) == 59


def upgrade() -> None:
    """Create games table and seed the initial game catalog."""

    game_category_enum = sa.Enum(
        "MOBA",
        "FPS",
        "RPG",
        "RACING",
        "CARD",
        "SPORTS",
        "STRATEGY",
        "FIGHTING",
        "SURVIVAL",
        "RHYTHM",
        name="game_category_enum",
    )
    game_category_enum.create(op.get_bind(), checkfirst=True)

    game_platform_enum = sa.Enum(
        "MOBILE",
        "PC",
        "BOTH",
        name="game_platform_enum",
    )
    game_platform_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("english_name", sa.String(length=150), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "MOBA",
                "FPS",
                "RPG",
                "RACING",
                "CARD",
                "SPORTS",
                "STRATEGY",
                "FIGHTING",
                "SURVIVAL",
                "RHYTHM",
                name="game_category_enum",
            ),
            nullable=False,
        ),
        sa.Column(
            "platform",
            sa.Enum("MOBILE", "PC", "BOTH", name="game_platform_enum"),
            nullable=False,
        ),
        sa.Column("icon_url", sa.String(length=500), nullable=True),
        sa.Column("cover_url", sa.String(length=500), nullable=True),
        sa.Column("color_theme", sa.String(length=7), nullable=True),
        sa.Column("service_template", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_games")),
    )
    op.create_index(op.f("ix_games_id"), "games", ["id"], unique=False)
    op.create_index(op.f("ix_games_name"), "games", ["name"], unique=False)
    op.create_index(op.f("ix_games_category"), "games", ["category"], unique=False)
    op.create_index(op.f("ix_games_platform"), "games", ["platform"], unique=False)
    op.create_index(op.f("ix_games_is_active"), "games", ["is_active"], unique=False)
    op.create_index(op.f("ix_games_sort_order"), "games", ["sort_order"], unique=False)

    games_table = sa.table(
        "games",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String(length=100)),
        sa.column("english_name", sa.String(length=150)),
        sa.column("category", sa.String(length=20)),
        sa.column("platform", sa.String(length=20)),
        sa.column("icon_url", sa.String(length=500)),
        sa.column("cover_url", sa.String(length=500)),
        sa.column("color_theme", sa.String(length=7)),
        sa.column("service_template", sa.JSON()),
        sa.column("description", sa.String(length=100)),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(games_table, GAME_SEEDS)


def downgrade() -> None:
    """Drop games table and related enum types."""

    op.drop_index(op.f("ix_games_sort_order"), table_name="games")
    op.drop_index(op.f("ix_games_is_active"), table_name="games")
    op.drop_index(op.f("ix_games_platform"), table_name="games")
    op.drop_index(op.f("ix_games_category"), table_name="games")
    op.drop_index(op.f("ix_games_name"), table_name="games")
    op.drop_index(op.f("ix_games_id"), table_name="games")
    op.drop_table("games")

    sa.Enum(name="game_platform_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="game_category_enum").drop(op.get_bind(), checkfirst=True)
