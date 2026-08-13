from __future__ import annotations

import re


COUNTRIES_ZH = {
    "Argentina": "阿根廷", "Austria": "奥地利", "Belgium": "比利时", "Bolivia": "玻利维亚",
    "Brazil": "巴西", "Bulgaria": "保加利亚", "Chile": "智利", "China": "中国",
    "Colombia": "哥伦比亚", "Croatia": "克罗地亚", "Denmark": "丹麦", "Ecuador": "厄瓜多尔",
    "England": "英格兰", "Estonia": "爱沙尼亚", "Finland": "芬兰", "France": "法国",
    "Germany": "德国", "Greece": "希腊", "Iceland": "冰岛", "India": "印度",
    "Italy": "意大利", "Japan": "日本", "Latvia": "拉脱维亚", "Lebanon": "黎巴嫩",
    "Lithuania": "立陶宛", "Mexico": "墨西哥", "Netherlands": "荷兰", "Norway": "挪威",
    "Paraguay": "巴拉圭", "Peru": "秘鲁", "Poland": "波兰", "Portugal": "葡萄牙",
    "Romania": "罗马尼亚", "Russia": "俄罗斯", "Scotland": "苏格兰", "Spain": "西班牙",
    "Sweden": "瑞典", "Switzerland": "瑞士", "Turkey": "土耳其", "Ukraine": "乌克兰",
    "Uruguay": "乌拉圭", "USA": "美国", "World": "国际",
}

COUNTRY_CODES = {
    "England": "GB", "Spain": "ES", "Germany": "DE", "Italy": "IT", "France": "FR",
    "Sweden": "SE", "Norway": "NO", "Finland": "FI",
}

COMPETITIONS_ZH = {
    ("England", "Premier League"): "英格兰超级联赛",
    ("England", "Championship"): "英格兰冠军联赛",
    ("Spain", "La Liga"): "西班牙甲级联赛",
    ("Spain", "Primera Division"): "西班牙甲级联赛",
    ("Germany", "Bundesliga"): "德国甲级联赛",
    ("Italy", "Serie A"): "意大利甲级联赛",
    ("France", "Ligue 1"): "法国甲级联赛",
    ("World", "Friendlies Clubs"): "俱乐部友谊赛",
    ("Argentina", "Primera Nacional"): "阿根廷全国联赛",
    ("Argentina", "Primera C"): "阿根廷丙级联赛",
    ("Argentina", "Torneo Promocional Amateur"): "阿根廷业余晋级联赛",
    ("Bolivia", "Primera División"): "玻利维亚甲级联赛",
    ("Brazil", "Copa Paulista"): "巴西圣保罗杯",
    ("Brazil", "Copa Rio"): "巴西里约杯",
    ("Bulgaria", "First League"): "保加利亚甲级联赛",
    ("Ecuador", "Liga Pro"): "厄瓜多尔甲级联赛",
    ("Estonia", "Esiliiga B"): "爱沙尼亚乙级B联赛",
    ("Finland", "Veikkausliiga"): "芬兰超级联赛",
    ("Finland", "Ykkönen"): "芬兰甲级联赛",
    ("Iceland", "1. Deild"): "冰岛甲级联赛",
    ("Iceland", "Úrvalsdeild"): "冰岛超级联赛",
    ("India", "Calcutta Premier Division"): "加尔各答超级联赛",
    ("Latvia", "1. Liga"): "拉脱维亚甲级联赛",
    ("Latvia", "Virsliga"): "拉脱维亚超级联赛",
    ("Lebanon", "Premier League"): "黎巴嫩超级联赛",
    ("Lithuania", "1 Lyga"): "立陶宛甲级联赛",
    ("Lithuania", "A Lyga"): "立陶宛超级联赛",
    ("Paraguay", "Division Intermedia"): "巴拉圭乙级联赛",
    ("Peru", "Primera División"): "秘鲁甲级联赛",
    ("Peru", "Segunda División"): "秘鲁乙级联赛",
    ("Romania", "Liga I"): "罗马尼亚甲级联赛",
    ("Russia", "First League"): "俄罗斯甲级联赛",
    ("Sweden", "Allsvenskan"): "瑞典超级联赛",
    ("Sweden", "Superettan"): "瑞典甲级联赛",
    ("USA", "MLS Next Pro"): "美国职业足球发展联赛",
    ("USA", "USL League Two"): "美国足球乙级联赛",
    ("Uruguay", "Primera División - Apertura"): "乌拉圭甲级联赛·春季赛",
    ("Norway", "Eliteserien"): "挪威超级联赛",
}

# 项目需求边界：五大联赛，加上三个北欧顶级联赛。使用国家与赛事名
# 联合判断，避免把其他国家同名的 Premier League / Serie A 混入。
SUPPORTED_COMPETITIONS = {
    ("England", "Premier League"),
    ("Spain", "La Liga"),
    ("Spain", "Primera Division"),
    ("Spain", "Primera División"),
    ("Germany", "Bundesliga"),
    ("Italy", "Serie A"),
    ("France", "Ligue 1"),
    ("Sweden", "Allsvenskan"),
    ("Norway", "Eliteserien"),
    ("Finland", "Veikkausliiga"),
}

COMPETITION_CODES = {
    ("England", "Premier League"): "PL",
    ("Spain", "La Liga"): "PD",
    ("Spain", "Primera Division"): "PD",
    ("Spain", "Primera División"): "PD",
    ("Germany", "Bundesliga"): "BL1",
    ("Italy", "Serie A"): "SA",
    ("France", "Ligue 1"): "FL1",
    ("Sweden", "Allsvenskan"): "SE1",
    ("Norway", "Eliteserien"): "NO1",
    ("Finland", "Veikkausliiga"): "FI1",
}

TEAMS_ZH = {
    # 英格兰
    "Arsenal": "阿森纳", "Arsenal FC": "阿森纳", "Aston Villa": "阿斯顿维拉",
    "Chelsea": "切尔西", "Liverpool": "利物浦", "Liverpool FC": "利物浦",
    "Manchester City": "曼城", "Manchester United": "曼联", "Newcastle": "纽卡斯尔联",
    "Newcastle United": "纽卡斯尔联", "Tottenham": "托特纳姆热刺", "Tottenham Hotspur": "托特纳姆热刺",
    "West Ham": "西汉姆联", "West Ham United": "西汉姆联", "Wolves": "伍尔弗汉普顿流浪者",
    "Everton": "埃弗顿", "Fulham": "富勒姆", "Crystal Palace": "水晶宫",
    "Brighton": "布莱顿", "Brentford": "布伦特福德", "Nottingham Forest": "诺丁汉森林",
    "Cambridge United": "剑桥联", "Braintree": "布伦特里",
    # 西班牙
    "Real Madrid": "皇家马德里", "FC Barcelona": "巴塞罗那", "Barcelona": "巴塞罗那",
    "Atletico Madrid": "马德里竞技", "Atlético de Madrid": "马德里竞技", "Athletic Club": "毕尔巴鄂竞技",
    "Real Sociedad": "皇家社会", "Villarreal": "比利亚雷亚尔", "Real Betis": "皇家贝蒂斯",
    "Sevilla": "塞维利亚", "Valencia": "瓦伦西亚", "Girona": "赫罗纳",
    # 德国
    "Bayern Munich": "拜仁慕尼黑", "FC Bayern München": "拜仁慕尼黑", "Borussia Dortmund": "多特蒙德",
    "RB Leipzig": "RB莱比锡", "Bayer Leverkusen": "勒沃库森", "Eintracht Frankfurt": "法兰克福",
    "VfB Stuttgart": "斯图加特", "SC Freiburg": "弗赖堡", "VfL Wolfsburg": "沃尔夫斯堡",
    # 意大利
    "Inter": "国际米兰", "Inter Milan": "国际米兰", "AC Milan": "AC米兰", "Juventus": "尤文图斯",
    "Napoli": "那不勒斯", "AS Roma": "罗马", "Roma": "罗马", "Lazio": "拉齐奥",
    "Atalanta": "亚特兰大", "Fiorentina": "佛罗伦萨", "Bologna": "博洛尼亚",
    # 法国及当前常见赛事球队
    "Paris Saint Germain": "巴黎圣日耳曼", "Paris Saint-Germain": "巴黎圣日耳曼", "Marseille": "马赛",
    "Lyon": "里昂", "Monaco": "摩纳哥", "Lille": "里尔", "Nice": "尼斯", "Strasbourg": "斯特拉斯堡",
    "Sporting CP": "葡萄牙体育", "Wolfsberger AC": "沃尔夫斯贝格",
    "CSKA Sofia": "索菲亚中央陆军", "Botev Plovdiv": "普罗夫迪夫博特夫",
    "Slavia Sofia": "索菲亚斯拉维亚", "Lokomotiv Sofia": "索菲亚火车头",
    "Malmo FF": "马尔默", "Djurgardens IF": "尤尔加登", "Kalmar FF": "卡尔马",
    "Orgryte IS": "奥尔格里特", "Ilves": "埃尔维斯", "Lahti": "拉赫蒂",
    "Mariehamn": "玛丽港", "Turku PS": "图尔库TPS",
    "Emelec": "埃梅莱克", "Independiente del Valle": "山谷独立",
    "Alianza Lima": "利马联盟", "Liverpool Montevideo": "蒙得维的亚利物浦",
    "Defensor Sporting": "防卫者体育", "Danubio": "达努比奥", "Volta Redonda": "沃尔塔雷东达",
}

# Current top-flight aliases as they appear in the audited Football-Data
# archives. Keeping the exact provider spelling here makes localization
# deterministic and preserves the original name beside the Chinese label.
TEAMS_ZH.update({
    # England
    "Bournemouth": "伯恩茅斯", "Burnley": "伯恩利", "Leeds": "利兹联",
    "Man City": "曼城", "Man United": "曼联", "Nott'm Forest": "诺丁汉森林",
    "Sunderland": "桑德兰",
    # Spain
    "Alaves": "阿拉维斯", "Ath Bilbao": "毕尔巴鄂竞技", "Ath Madrid": "马德里竞技",
    "Betis": "皇家贝蒂斯", "Celta": "塞尔塔", "Elche": "埃尔切",
    "Espanol": "西班牙人", "Getafe": "赫塔费", "Levante": "莱万特",
    "Mallorca": "马略卡", "Osasuna": "奥萨苏纳", "Oviedo": "皇家奥维耶多",
    "Sociedad": "皇家社会", "Vallecano": "巴列卡诺",
    # Germany
    "Augsburg": "奥格斯堡", "Dortmund": "多特蒙德", "Ein Frankfurt": "法兰克福",
    "FC Koln": "科隆", "Freiburg": "弗赖堡", "Hamburg": "汉堡",
    "Heidenheim": "海登海姆", "Hoffenheim": "霍芬海姆", "Leverkusen": "勒沃库森",
    "M'gladbach": "门兴格拉德巴赫", "Mainz": "美因茨", "St Pauli": "圣保利",
    "Stuttgart": "斯图加特", "Union Berlin": "柏林联合",
    "Werder Bremen": "云达不来梅", "Wolfsburg": "沃尔夫斯堡",
    # Italy
    "Cagliari": "卡利亚里", "Como": "科莫", "Cremonese": "克雷莫内塞",
    "Genoa": "热那亚", "Lecce": "莱切", "Milan": "AC米兰",
    "Parma": "帕尔马", "Pisa": "比萨", "Sassuolo": "萨索洛",
    "Torino": "都灵", "Udinese": "乌迪内斯", "Verona": "维罗纳",
    # France
    "Angers": "昂热", "Auxerre": "欧塞尔", "Brest": "布雷斯特",
    "Le Havre": "勒阿弗尔", "Lens": "朗斯", "Lorient": "洛里昂",
    "Metz": "梅斯", "Nantes": "南特", "Paris FC": "巴黎FC",
    "Paris SG": "巴黎圣日耳曼", "Rennes": "雷恩", "Toulouse": "图卢兹",
    # Sweden
    "AIK": "AIK索尔纳", "Brommapojkarna": "布洛马波卡纳", "Degerfors": "代格福什",
    "Djurgarden": "尤尔加登", "Elfsborg": "埃尔夫斯堡", "GAIS": "哥德堡盖斯",
    "Goteborg": "哥德堡", "Hacken": "赫根", "Halmstad": "哈尔姆斯塔德",
    "Hammarby": "哈马比", "Kalmar": "卡尔马", "Mjallby": "米亚尔比",
    "Orgryte": "奥尔格里特", "Sirius": "天狼星", "Vasteras SK": "韦斯特罗斯",
    # Finland
    "AC Oulu": "奥卢", "Gnistan": "格尼斯坦", "HJK": "赫尔辛基",
    "Inter Turku": "图尔库国际", "Jaro": "雅罗", "KuPS": "古比斯",
    "SJK": "塞伊奈约基", "TPS": "图尔库TPS", "VPS": "瓦萨",
    # Norway
    "Aalesund": "奥勒松", "Bodo/Glimt": "博德闪耀", "Brann": "布兰",
    "Fredrikstad": "腓特烈斯塔", "HamKam": "汉坎", "KFUM Oslo": "奥斯陆KFUM",
    "Kristiansund": "克里斯蒂安松", "Lillestrom": "利勒斯特罗姆",
    "Molde": "莫尔德", "Rosenborg": "罗森博格", "Sandefjord": "桑讷菲尤尔",
    "Sarpsborg 08": "萨普斯堡08", "Start": "斯达", "Tromso": "特罗姆瑟",
    "Valerenga": "瓦勒伦加", "Viking": "维京",
})


def localize_country(name: str | None) -> str | None:
    return COUNTRIES_ZH.get(name, name) if name else None


def localize_competition(country: str | None, name: str) -> str:
    return COMPETITIONS_ZH.get((country or "", name), name)


def localize_team(name: str | None) -> str | None:
    return TEAMS_ZH.get(name, name) if name else None


def localize_round(value: str | None) -> str | None:
    if not value:
        return None
    patterns = (
        (r"^Regular Season\s*-\s*(\d+)$", r"常规赛 · 第\1轮"),
        (r"^Matchday\s+(\d+)$", r"第\1轮"),
        (r"^Round of 16$", "十六强"),
        (r"^Quarter-finals?$", "四分之一决赛"),
        (r"^Semi-finals?$", "半决赛"),
        (r"^Final$", "决赛"),
    )
    for pattern, replacement in patterns:
        if re.match(pattern, value, flags=re.IGNORECASE):
            return re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


def is_supported_competition(country: str | None, name: str) -> bool:
    return (country or "", name) in SUPPORTED_COMPETITIONS


def canonical_competition_code(country: str | None, name: str) -> str:
    return COMPETITION_CODES.get((country or "", name), "UNKNOWN")


def country_code(country: str | None) -> str:
    return COUNTRY_CODES.get(country or "", "ZZ")
