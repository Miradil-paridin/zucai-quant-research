"""竞彩中文队名 -> The Odds API 英文队名映射。

价差分析用它把 jc_matches 与 consensus_odds_snapshots 关联。
未映射的队会在分析输出的 unmatched 中列出, 随联赛开售逐步补充。
"""

from __future__ import annotations

JC_TO_CONSENSUS: dict[str, str] = {
    # 挪威超级联赛
    "博德闪耀": "Bodø/Glimt",
    "利勒斯特罗姆": "Lillestrom",
    "瓦勒伦加": "Vålerenga",
    "哈姆卡姆": "HamKam",
    "弗雷德利克斯塔": "Fredrikstad FK",
    "桑德菲杰": "Sandefjord",
    "斯达": "IK Start",
    "维京": "Viking FK",
    "KFUM奥斯陆": "KFUM",
    "克里斯蒂安松": "Kristiansund BK",
    "奥勒松": "Aalesund",
    "特罗姆瑟": "Tromso",
    "莫尔德": "Molde",
    "萨尔普斯堡": "Sarpsborg FK",
    "布兰": "SK Brann",
    "罗森博格": "Rosenborg",
    # 美国职业大联盟
    "纽约城": "New York City FC",
    "多伦多FC": "Toronto FC",
    "迈阿密国际": "Inter Miami CF",
    "哥伦布机员": "Columbus Crew SC",
    "温哥华白帽": "Vancouver Whitecaps FC",
    "洛杉矶FC": "Los Angeles FC",
    "蒙特利尔": "CF Montreal",
    "新英格兰革命": "New England Revolution",
    "华盛顿联": "D.C. United",
    "纳什维尔": "Nashville SC",
    "纽约红牛": "New York Red Bulls",
    "奥兰多城": "Orlando City SC",
    "辛辛那提": "FC Cincinnati",
    "圣何塞地震": "San Jose Earthquakes",
    "费城联合": "Philadelphia Union",
    "亚特兰大联": "Atlanta United FC",
    "圣路易斯城": "St. Louis City SC",
    "皇家盐湖城": "Real Salt Lake",
    "堪萨斯城竞技": "Sporting Kansas City",
    "休斯敦迪纳摩": "Houston Dynamo",
    "明尼苏达联": "Minnesota United FC",
    "圣迭戈FC": "San Diego FC",
    "芝加哥火焰": "Chicago Fire",
    "夏洛特FC": "Charlotte FC",
    "科罗拉多急流": "Colorado Rapids",
    "奥斯汀FC": "Austin FC",
    "洛杉矶银河": "LA Galaxy",
    "达拉斯FC": "FC Dallas",
    "波特兰伐木者": "Portland Timbers",
    "西雅图海湾人": "Seattle Sounders FC",
    # 韩国职业联赛
    "江原FC": "Gangwon FC",
    "富川FC": "Bucheon FC 1995",
    "全北现代": "Jeonbuk Hyundai Motors",
    "首尔FC": "FC Seoul",
    "浦项制铁": "Pohang Steelers",
    "金泉尚武": "Sangju Sangmu FC",
    "蔚山现代": "Ulsan Hyundai FC",
    "蔚山HD": "Ulsan Hyundai FC",
    "安养FC": "FC Anyang",
    "济州SK": "Jeju United FC",
    "仁川联": "Incheon United",
    "大田市民": "Daejeon Citizen",
    "光州FC": "Gwangju FC",
}


# 竞彩中文简称 -> football-data.co.uk 英文队名(五大联赛, 用于历史价差回测)
JC_TO_FD: dict[str, str] = {
    # 英超
    "伊普斯": "Ipswich", "伯恩茅斯": "Bournemouth", "切尔西": "Chelsea",
    "利物浦": "Liverpool", "南安普敦": "Southampton", "埃弗顿": "Everton",
    "富勒姆": "Fulham", "布伦特": "Brentford", "布赖顿": "Brighton",
    "曼城": "Man City", "曼联": "Man United", "水晶宫": "Crystal Palace",
    "热刺": "Tottenham", "狼队": "Wolves", "纽卡斯尔": "Newcastle",
    "维拉": "Aston Villa", "莱切斯特": "Leicester", "西汉姆联": "West Ham",
    "诺丁汉": "Nott'm Forest", "阿森纳": "Arsenal",
    "桑德兰": "Sunderland", "利兹联": "Leeds", "伯恩利": "Burnley",
    # 西甲
    "塞尔塔": "Celta", "塞维利亚": "Sevilla", "奥萨苏纳": "Osasuna",
    "巴伦西亚": "Valencia", "巴列卡诺": "Vallecano", "巴利亚多": "Valladolid",
    "巴萨": "Barcelona", "拉帕马斯": "Las Palmas", "比利亚雷": "Villarreal",
    "毕尔巴鄂": "Ath Bilbao", "皇家社会": "Sociedad", "皇马": "Real Madrid",
    "莱加内斯": "Leganes", "西班牙人": "Espanol", "贝蒂斯": "Betis",
    "赫塔费": "Getafe", "赫罗纳": "Girona", "阿拉维斯": "Alaves",
    "马洛卡": "Mallorca", "马竞": "Ath Madrid",
    "埃尔切": "Elche", "莱万特": "Levante", "奥维耶多": "Oviedo",
    # 德甲
    "不来梅": "Werder Bremen", "勒沃库森": "Leverkusen", "圣保利": "St Pauli",
    "基尔": "Holstein Kiel", "多特蒙德": "Dortmund", "奥格斯堡": "Augsburg",
    "弗赖堡": "Freiburg", "拜仁": "Bayern Munich", "斯图加特": "Stuttgart",
    "柏林联合": "Union Berlin", "沃夫斯堡": "Wolfsburg", "法兰克福": "Ein Frankfurt",
    "波鸿": "Bochum", "海登海姆": "Heidenheim", "美因茨": "Mainz",
    "莱红牛": "RB Leipzig", "门兴": "M'gladbach", "霍芬海姆": "Hoffenheim",
    "汉堡": "Hamburg", "科隆": "FC Koln",
    # 意甲
    "AC米兰": "Milan", "乌迪内斯": "Udinese", "亚特兰大": "Atalanta",
    "佛罗伦萨": "Fiorentina", "博洛尼亚": "Bologna", "卡利亚里": "Cagliari",
    "国际米兰": "Inter", "威尼斯": "Venezia", "尤文图斯": "Juventus",
    "帕尔马": "Parma", "恩波利": "Empoli", "拉齐奥": "Lazio",
    "热那亚": "Genoa", "科莫": "Como", "维罗纳": "Verona", "罗马": "Roma",
    "莱切": "Lecce", "蒙扎": "Monza", "那不勒斯": "Napoli", "都灵": "Torino",
    "克雷莫纳": "Cremonese", "比萨": "Pisa", "萨索洛": "Sassuolo",
    # 法甲
    "兰斯": "Reims", "勒阿弗尔": "Le Havre", "南特": "Nantes",
    "图卢兹": "Toulouse", "圣埃蒂安": "St Etienne", "尼斯": "Nice",
    "巴黎圣曼": "Paris SG", "布雷斯特": "Brest", "摩纳哥": "Monaco",
    "斯特拉斯": "Strasbourg", "昂热": "Angers", "朗斯": "Lens",
    "欧塞尔": "Auxerre", "蒙彼利埃": "Montpellier", "里尔": "Lille",
    "里昂": "Lyon", "雷恩": "Rennes", "马赛": "Marseille",
    "洛里昂": "Lorient", "梅斯": "Metz", "巴黎FC": "Paris FC",
}
