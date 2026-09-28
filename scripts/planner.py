#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from providers import normalize_category, search_pois, set_request_deadline

try:
    from amap_ip_location import get_ip_location
    IP_LOCATION_AVAILABLE = True
except ImportError:
    IP_LOCATION_AVAILABLE = False

try:
    from macos_location import get_macos_location
    MACOS_LOCATION_AVAILABLE = True
except ImportError:
    MACOS_LOCATION_AVAILABLE = False

SKILL_DIR = Path(__file__).resolve().parents[1]
UNIFIED_SEARCH = SKILL_DIR.parent / "unified-search" / "scripts" / "unified-search.sh"
ANCHORS_FILE = SKILL_DIR / "config" / "anchors.json"

SEARCH_PROVIDER_LIMIT = 15
RECOMMEND_PROVIDER_LIMIT = 8
SEARCH_WEB_VERIFY_LIMIT = 5
RECOMMEND_WEB_VERIFY_LIMIT = 3
SEARCH_DISPLAY_LIMIT = 10
RECOMMEND_DISPLAY_LIMIT = 3

CATEGORY_KEYWORDS = {
    "dessert": ["甜品", "冰淇淋", "酸奶", "gelato", "冰沙"],
    "cafe": ["咖啡", "咖啡店", "咖啡馆", "下午茶"],
    "tea": ["奶茶", "茶饮", "饮品", "果茶"],
    "bakery": ["面包", "蛋糕", "烘焙", "面包店"],
    "restaurant": ["饭馆", "餐厅", "美食", "吃饭"],
    "网吧": ["网吧", "网咖", "电竞馆"],
    "KTV": ["KTV", "唱歌", "量贩KTV"],
    "电影院": ["电影院", "影城", "看电影"],
    "酒店": ["酒店", "宾馆", "住宿", "旅馆", "民宿"],
    "医院": ["医院", "诊所", "综合医院"],
    "药店": ["药店", "药房", "大药房"],
    "快递": ["快递", "物流", "菜鸟驿站"],
    "健身房": ["健身房", "游泳", "健身"],
    "超市": ["超市", "便利店", "小卖部"],
    "银行": ["银行", "ATM", "存取款"],
    "充电站": ["充电桩", "充电站", "加油站"],
    "桌游": ["桌游", "剧本杀", "密室逃脱"],
    "台球": ["台球", "桌球"],
    "理发": ["理发", "美发"],
    "宠物": ["宠物"],
    "按摩": ["足疗", "按摩"],
}

CATEGORY_EVIDENCE_HINTS = {
    "dessert": ["甜品", "冰淇淋", "酸奶", "gelato", "咖啡甜品"],
    "cafe": ["咖啡", "咖啡馆", "下午茶", "拿铁"],
    "tea": ["奶茶", "茶饮", "果茶", "柠檬茶"],
    "bakery": ["面包", "蛋糕", "烘焙", "吐司"],
    "restaurant": ["饭馆", "餐厅", "美食", "火锅", "烧烤", "面馆", "小吃", "人均"],
    "电影院": ["电影院", "看电影", "影院", "影城", "影厅", "电影", "IMAX", "4DX", "杜比", "CINITY", "巨幕"],
}

CATEGORY_ALIASES = {
    "dessert": "dessert",
    "甜品": "dessert",
    "冰淇淋": "dessert",
    "酸奶": "dessert",
    "gelato": "dessert",
    "cafe": "cafe",
    "咖啡": "cafe",
    "咖啡店": "cafe",
    "咖啡馆": "cafe",
    "tea": "tea",
    "奶茶": "tea",
    "茶饮": "tea",
    "饮品": "tea",
    "bakery": "bakery",
    "面包": "bakery",
    "蛋糕": "bakery",
    "烘焙": "bakery",
    "restaurant": "restaurant",
    "dinner": "restaurant",
    "餐厅": "restaurant",
    "饭馆": "restaurant",
    "饭店": "restaurant",
    "吃饭": "restaurant",
    "晚餐": "restaurant",
    "馆子": "restaurant",
    "美食": "restaurant",
    "火锅": "restaurant",
    "烧烤": "restaurant",
    "面馆": "restaurant",
    # Non-food categories (aligned with amap_poi TYPECODE_MAP)
    "网吧": "网吧",
    "网咖": "网吧",
    "电竞": "网吧",
    "KTV": "KTV",
    "唱歌": "KTV",
    "电影院": "电影院",
    "看电影": "电影院",
    "影院": "电影院",
    "4D电影院": "电影院",
    "酒店": "酒店",
    "宾馆": "酒店",
    "旅馆": "酒店",
    "住宿": "酒店",
    "民宿": "酒店",
    "医院": "医院",
    "诊所": "医院",
    "药店": "药店",
    "药房": "药店",
    "快递": "快递",
    "物流": "快递",
    "菜鸟": "快递",
    "健身房": "健身房",
    "游泳馆": "健身房",
    "超市": "超市",
    "便利店": "超市",
    "银行": "银行",
    "加油站": "加油站",
    "充电桩": "充电站",
    "充电站": "充电站",
    "桌游": "桌游",
    "剧本杀": "桌游",
    "密室": "桌游",
    "台球": "台球",
    "桌球": "台球",
    "理发": "理发",
    "美发": "理发",
    "宠物": "宠物",
    "足疗": "按摩",
    "按摩": "按摩",
}

MODE_ALIASES = {
    "search": "search",
    "recommend": "recommend",
}

PREFERENCE_HINTS = {
    "yogurt": ["酸奶", "yogurt"],
    "gelato": ["gelato", "手工冰淇淋", "意式冰淇淋"],
    "smoothie": ["冰沙", "果昔", "smoothie"],
    "light": ["清爽", "不腻", "轻食感"],
}

CONSTRAINT_HINTS = {
    "metro": ["地铁可达", "近地铁", "地铁站附近"],
    "seating": ["有座位", "堂食", "能坐着"],
    "environment": ["环境好", "适合约会", "安静", "适合聊天"],
    "mall": ["商场", "购物中心", "mall"],
}

AVOID_HINTS = {
    "night_market": ["夜市", "小吃街"],
    "takeaway_only": ["外带", "窗口", "档口"],
    "no_seating": ["无座位", "站着吃"],
}

CITYISH_RE = re.compile(r"(city8\.com|城市吧|map|地图|购物中心|商场|店|餐厅|饭馆|美食|甜品|冰淇淋|咖啡)", re.I)
RADIUS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(km|KM|公里|千米|m|M|米)")
EXPLICIT_MODE_RE = re.compile(r"\bmode\s*=\s*(search|recommend)\b", re.I)

SEARCH_HINTS = ["有哪些", "附近有什么", "帮我找", "找找", "列几个", "列出", "给我看看", "周边", "附近的", "附近", "2km", "1km", "3km"]
RECOMMEND_HINTS = ["推荐一家", "推荐一个", "帮我选", "挑一个", "最适合", "最值得", "帮我拍板", "首选", "哪个最好", "哪家最好"]


def load_anchors():
    if ANCHORS_FILE.exists():
        return json.loads(ANCHORS_FILE.read_text())
    return {}


def normalize_category(value: str):
    raw = (value or "").strip().lower()
    if not raw:
        return "restaurant"
    if raw == "ktv":
        return "KTV"
    if raw in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[raw]
    for key, canonical in sorted(CATEGORY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if key.lower() in raw:
            return canonical
    return raw or "restaurant"


def normalize_mode(value: str):
    raw = (value or "").strip().lower()
    return MODE_ALIASES.get(raw, None)


def infer_mode(query: str):
    q = (query or "").strip()
    if not q:
        return "search"
    explicit = EXPLICIT_MODE_RE.search(q)
    if explicit:
        return explicit.group(1).lower()
    lowered = q.lower()
    if any(token.lower() in lowered for token in RECOMMEND_HINTS):
        return "recommend"
    if any(token.lower() in lowered for token in SEARCH_HINTS):
        return "search"
    return "search"


def _strip_negated_category_terms(query: str):
    text = query or ""
    negative = r"(?:不要|不想(?:要|吃)?|不喜欢|不需要|别要|别去|不去|避开|排除)\s*"
    terms = sorted(CATEGORY_ALIASES, key=len, reverse=True)
    for term in terms:
        text = re.sub(negative + re.escape(term), " ", text, flags=re.I)
    return text


def infer_category(query: str):
    q = _strip_negated_category_terms(query).lower()
    if any(x in q for x in ["甜品", "gelato", "酸奶", "冰淇淋", "dessert"]):
        return "dessert"
    if any(x in q for x in ["咖啡", "cafe"]):
        return "cafe"
    if any(x in q for x in ["奶茶", "茶饮", "果茶", "饮品"]):
        return "tea"
    if any(x in q for x in ["面包", "蛋糕", "烘焙", "bakery"]):
        return "bakery"
    if any(x in q for x in ["晚餐", "餐厅", "饭馆", "饭店", "吃饭", "馆子", "restaurant", "dinner", "美食", "火锅", "烧烤"]):
        return "restaurant"
    # Non-food categories
    if any(x in q for x in ["网吧", "网咖", "电竞"]):
        return "网吧"
    if any(x in q for x in ["ktv", "唱歌", "卡拉"]):
        return "KTV"
    if any(x in q for x in ["电影院", "看电影", "影院"]):
        return "电影院"
    if any(x in q for x in ["酒店", "宾馆", "旅馆", "住宿", "民宿"]):
        return "酒店"
    if any(x in q for x in ["医院", "诊所", "社区医院"]):
        return "医院"
    if any(x in q for x in ["药店", "药房"]):
        return "药店"
    if any(x in q for x in ["快递", "物流", "菜鸟"]):
        return "快递"
    if any(x in q for x in ["健身房", "健身", "游泳", "泳池", "游泳馆", "水上乐园", "运动", "锻炼", "gym", "fitness", "swim", "pool"]):
        return "健身房"
    if any(x in q for x in ["超市", "便利店", "小卖部"]):
        return "超市"
    if any(x in q for x in ["银行", "atm", "存取款"]):
        return "银行"
    if "加油站" in q:
        return "加油站"
    if any(x in q for x in ["充电桩", "充电站"]):
        return "充电站"
    if any(x in q for x in ["桌游", "剧本杀", "密室"]):
        return "桌游"
    if any(x in q for x in ["台球", "桌球"]):
        return "台球"
    if any(x in q for x in ["理发", "美发", "剪发"]):
        return "理发"
    if any(x in q for x in ["宠物"]):
        return "宠物"
    if any(x in q for x in ["足疗", "按摩", "推拿"]):
        return "按摩"
    return None


def _extract_fallback_keywords(query: str):
    """When infer_category returns None, extract meaningful search terms from the original query
    for direct Amap keyword search. Removes noise words, location hints, and pronouns."""
    if not query:
        return None
    noise = r'(我|我们|咱|咱们|自己|我这|我这里|我的位置|我的地点|当前位置|附近的|附近|周边的|周边|周围的|周围|方圆|出发|去|找|搜索|有没有|有什么|给我|帮我|我要|我想|请问|可以|能|怎么|哪里|哪有|哪儿|有的|一些|一下|个|的)'
    q = re.sub(noise, ' ', query)
    q = re.sub(r'\s+', ' ', q).strip()
    return q if q and len(q) >= 2 else None


def split_csv(s):
    if not s:
        return []
    return [x.strip() for x in re.split(r"[,，]", s) if x.strip()]


def parse_radius_m(text: str, default_m: int = 3000):
    if not text:
        return default_m
    m = RADIUS_RE.search(text)
    if not m:
        return default_m
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit in {"km", "公里", "千米"}:
        return int(value * 1000)
    return int(value)


def clean_origin_text(origin: str):
    """Clean origin text. Returns empty string for self-references so caller falls back to '未指定起点'."""
    if not origin:
        return origin
    origin = origin.strip(" ，,")
    # Query verbs captured by the broad nearby/outgoing regex are not places.
    changed = True
    while changed:
        changed = False
        for prefix in ("请问", "麻烦", "请", "帮我", "帮忙", "给我", "我想", "我要", "我需要"):
            if origin.startswith(prefix):
                origin = origin[len(prefix):].lstrip(" ，,")
                changed = True
                break
    for prefix in ("查找", "查一下", "搜索", "推荐", "看看", "找一下", "找找", "找", "查"):
        if origin.startswith(prefix):
            origin = origin[len(prefix):].lstrip(" ，,")
            break
    for prefix in ("从", "在", "离"):
        if origin.startswith(prefix) and len(origin) > len(prefix):
            origin = origin[len(prefix):].lstrip(" ，,")
            break
    origin = re.sub(r"\s*\d+(?:\.\d+)?\s*(?:km|KM|公里|千米|m|M|米)\s*$", "", origin)
    origin = re.sub(r"\s*(附近的|附近|周边的|周边)\s*$", "", origin)
    origin = origin.strip(" ，,")
    # Treat first-person pronouns as self-references → empty → fallback to "未指定起点" → triggers CoreLocation
    if origin and re.fullmatch(r"(我|我们|咱|咱们|自己|我这里|我这|我的位置|我的地点|当前位置)", origin):
        return ""
    return origin


def parse_request(args, deadline=None):
    query = args.query or ""
    origin = args.origin
    if not origin and query:
        m = re.search(r"(.+?)(附近|周边|出发)", query)
        if m:
            origin = clean_origin_text(m.group(1))
    positive_query = _strip_negated_category_terms(query)
    raw_cat = args.category or infer_category(query)
    if raw_cat is None:
        raw_cat = _extract_fallback_keywords(positive_query) or "restaurant"
    category = normalize_category(raw_cat)
    mode = normalize_mode(getattr(args, "mode", None)) or infer_mode(query)
    preferences = split_csv(args.preferences)
    constraints = split_csv(args.constraints)
    avoid = split_csv(args.avoid)
    radius_m = parse_radius_m(query)
    if radius_m == 3000:
        for c in constraints:
            candidate = parse_radius_m(c)
            if candidate != 3000:
                radius_m = candidate
                break

    if query and not preferences:
        q = query.lower()
        for key, hints in PREFERENCE_HINTS.items():
            if any(h in q for h in [x.lower() for x in hints]):
                preferences.append(key)
    if query and not constraints:
        q = query.lower()
        for key, hints in CONSTRAINT_HINTS.items():
            if any(h.lower() in q for h in hints):
                constraints.append(key)
    if query and not avoid:
        q = query.lower()
        negative = r"(?:不要|不想要|不想|不喜欢|避开|排除|别去|不去|别要)"
        for key, hints in AVOID_HINTS.items():
            if any(re.search(negative + r"[^，,。；;]{0,10}" + re.escape(h.lower()), q) for h in hints):
                avoid.append(key)

    origin_final = clean_origin_text(origin or "未指定起点")

    # Location resolution is explicit and policy-controlled. The default keeps
    # the existing CoreLocation -> IP fallback for nearby requests.
    ip_location = None
    location_policy = "corelocation" if getattr(args, "corelocation", False) else getattr(args, "location_policy", "auto")
    NEARBY_HINTS_RE = re.compile(r"(附近|周边|附近有什么|周围的|周围的店|方圆)")
    if origin_final == "未指定起点" and NEARBY_HINTS_RE.search(query) and location_policy != "disabled":
        if location_policy in {"auto", "corelocation"} and not getattr(args, "corelocation", False) and MACOS_LOCATION_AVAILABLE:
            try:
                remaining = 10 if deadline is None else min(10, deadline - time.monotonic())
                if remaining > 0:
                    ip_location = get_macos_location(timeout=remaining)
            except Exception:
                pass
        if not ip_location and location_policy in {"auto", "ip"} and IP_LOCATION_AVAILABLE:
            try:
                ip_location = get_ip_location(deadline=deadline)
            except Exception:
                pass

    return {
        "query": query,
        "origin": origin_final,
        "category": category,
        "mode": mode,
        "radius_m": radius_m,
        "preferences": sorted(set(preferences)),
        "constraints": sorted(set(constraints)),
        "avoid": sorted(set(avoid)),
        "ip_location": ip_location,
    }


def expand_anchors(origin):
    anchors_map = load_anchors()
    anchors = anchors_map.get(origin, [])
    if not anchors:
        anchors = [origin]
    uniq = []
    for x in [origin] + anchors:
        if x and x not in uniq:
            uniq.append(x)
    return uniq


def _reverse_geocode(lat, lon, deadline=None):
    """Convert GPS coordinates to a human-readable location via Amap regeo API."""
    key = os.getenv("AMAP_KEY", "")
    if not key:
        return None
    try:
        url = f"https://restapi.amap.com/v3/geocode/regeo?location={lon},{lat}&key={key}&radius=1000&extensions=base"
        req = urllib.request.Request(url)
        timeout = 5 if deadline is None else min(5, deadline - time.monotonic())
        if timeout <= 0:
            return None
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") == "1":
            comp = data["regeocode"]["addressComponent"]
            district = comp.get("district", "")
            township = comp.get("township", "")
            if township and township != "[]":
                return f"{district}{township}" if district else township
            return district or None
    except Exception:
        pass
    return None


def _resolve_gps_anchor(anchor, deadline=None):
    """If anchor is bare GPS coords, resolve to human-readable name."""
    m = re.match(r"^\s*(-?\d+\.?\d*)\s*[,，]\s*(-?\d+\.?\d*)\s*$", anchor)
    if m:
        resolved = _reverse_geocode(float(m.group(1)), float(m.group(2)), deadline=deadline)
        if resolved:
            return resolved
    return anchor


def build_queries(req, anchors, poi_candidates=None, mode="recommend", deadline=None):
    keywords = CATEGORY_KEYWORDS.get(req["category"], [req["category"]])
    pref_terms = []
    for p in req["preferences"]:
        pref_terms.extend(PREFERENCE_HINTS.get(p, [p]))
    cons_terms = []
    for c in req["constraints"]:
        cons_terms.extend(CONSTRAINT_HINTS.get(c, [c]))

    core_pref = "/".join(pref_terms[:3]) if pref_terms else keywords[0]
    core_cons = " ".join(cons_terms[:3]) if cons_terms else ("堂食 有座位" if req["category"] == "restaurant" else "环境好")

    queries = []
    if mode == "recommend" and poi_candidates:
        for poi in poi_candidates[:2]:
            queries.append(f"{poi['name']} {req['origin']} 评价 环境 人均")
    for a in anchors[:2]:
        a_display = _resolve_gps_anchor(a, deadline=deadline)
        queries.append(f"{a_display} {keywords[0]} {core_cons} {core_pref}")
        if mode == "search":
            queries.append(f"{a_display} {' '.join(keywords[:3])} 哪些值得去")

    out = []
    for q in queries:
        if q not in out:
            out.append(q)
    return out[: (2 if mode == 'recommend' else 3)]


def run_unified_search(query, deadline=None):
    if not UNIFIED_SEARCH.exists():
        return {"query": query, "ok": False, "output": "unified-search script not found"}
    # The automatic route already selects deep search and returns JSON. Do not
    # append legacy flags: its parser would incorporate their values into the query.
    cmd = ["bash", str(UNIFIED_SEARCH), query]
    timeout = 1800
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            return {
                "query": query,
                "ok": False,
                "count": None,
                "source_errors": [],
                "partial": False,
                "stdout": "",
                "stderr": "request deadline exhausted before unified-search started",
                "output": "request deadline exhausted before unified-search started",
                "contract_error": "request deadline exhausted",
            }
    try:
        child_env = os.environ.copy()
        child_env["UNIFIED_SEARCH_TIMEOUT_SECONDS"] = str(min(1800.0, timeout))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=child_env)
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        result_count = None
        contract_error = None
        search_payload = None
        try:
            search_payload = json.loads(proc.stdout or "")
        except json.JSONDecodeError:
            contract_error = "unified-search returned invalid JSON"

        if contract_error is None:
            if not isinstance(search_payload, dict):
                contract_error = "unified-search JSON output must be an object"
            elif not isinstance(search_payload.get("results"), list):
                contract_error = "unified-search JSON output is missing a results list"
            elif type(search_payload.get("count")) is not int or search_payload["count"] < 0:
                contract_error = "unified-search JSON output has an invalid count"
            elif search_payload["count"] != len(search_payload["results"]):
                contract_error = "unified-search count does not match its results list"
            elif search_payload.get("status") == "error":
                error_detail = search_payload.get("error")
                if isinstance(error_detail, dict):
                    error_detail = error_detail.get("message") or error_detail.get("code")
                contract_error = str(error_detail or "unified-search reported an error")
            else:
                result_count = search_payload["count"]
        source_errors = sorted({
            name.lower() for name in re.findall(
                r"\[(exa|tavily|grok|tinyfish)\]\s+error:", proc.stderr or "", re.I
            )
        })
        return {
            "query": query,
            "ok": proc.returncode == 0 and contract_error is None,
            "count": result_count,
            "contract_error": contract_error,
            "source_errors": source_errors,
            "partial": bool(source_errors) or (
                isinstance(search_payload, dict) and search_payload.get("status") == "partial"
            ),
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "output": text,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return {
            "query": query,
            "ok": False,
            "count": None,
            "source_errors": [],
            "partial": False,
            "stdout": stdout,
            "stderr": stderr,
            "output": "\n".join(part for part in (stdout, stderr, "timeout") if part),
        }
    except OSError as exc:
        message = f"unified-search could not start: {exc}"
        return {"query": query, "ok": False, "count": None, "source_errors": [], "partial": False, "stdout": "", "stderr": message, "output": message}


def extract_candidate_lines(output):
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        structured_lines = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(
                item.get("snippet") or item.get("native_summary") or
                item.get("summary_zh") or item.get("summary") or ""
            ).strip()
            if title:
                structured_lines.append(f"Title: {title}")
            if url:
                structured_lines.append(f"URL: {url}")
            if snippet:
                structured_lines.append(f"Snippet: {snippet}")
        return structured_lines[:20]

    lines = []
    skip_prefixes = (
        "🔍 综合搜索:",
        "Run Dir:",
        "Query:",
        "Engine Summary",
        "Hit Summary",
        "✅ 综合搜索完成",
        "━━━━━━━━━━━━━━━━",
    )
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(skip_prefixes):
            continue
        if line.startswith("Title:") or line.startswith("URL:"):
            lines.append(line)
        elif CITYISH_RE.search(line) and ("http" in line or "店" in line or "餐厅" in line or "饭馆" in line or "购物中心" in line or "商场" in line):
            lines.append(line)
    return lines[:20]


def _candidate_lines_from_run(run):
    # Search-layer emits JSON on stdout; stderr is diagnostics and must not be
    # mixed into evidence. Older legacy output remains supported as fallback.
    if "stdout" in run:
        return extract_candidate_lines(run.get("stdout") or "")
    return extract_candidate_lines(run.get("output", ""))


def _get_evidence_hints(category):
    """Auto-derive evidence hints from CATEGORY_EVIDENCE_HINTS or CATEGORY_ALIASES.

    If the category has curated hints in CATEGORY_EVIDENCE_HINTS, use those.
    Otherwise, collect all aliases that map to this category from CATEGORY_ALIASES.
    This means adding a new category to CATEGORY_ALIASES automatically generates hints.
    """
    if category in CATEGORY_EVIDENCE_HINTS:
        return CATEGORY_EVIDENCE_HINTS[category]
    hints = [category]
    for alias, canonical in CATEGORY_ALIASES.items():
        if canonical == category and alias != category:
            hints.append(alias)
    return hints


def score_evidence(req, candidate_lines):
    text = "\n".join(candidate_lines)
    score = 0
    hints = _get_evidence_hints(req["category"])

    if any(x in text for x in hints):
        score += 28
    if req["category"] == "restaurant" and any(x in text for x in ["人均", "美食", "餐厅", "饭馆", "火锅", "烧烤"]):
        score += 22
    if req["category"] != "restaurant" and any(x in text for x in ["环境", "堂食", "人均", "位置示意图", "交通指引"]):
        score += 18
    if req["category"] == "restaurant" and any(x in text for x in ["环境", "堂食", "位置", "营业时间", "评价"]):
        score += 18
    if any(x in text for x in ["购物中心", "商场", "广场"]):
        score += 10
    if req["origin"] in text:
        score += 10
    if any(a in text for a in expand_anchors(req["origin"])):
        score += 10
    if any(x in text for x in ["NO_EXACT_HIT", "bestHit: none", "none"]):
        score -= 10
    return max(0, min(100, score))


def _evidence_level(web_score):
    if web_score is None:
        return "none"
    if web_score >= 70:
        return "strong"
    if web_score >= 45:
        return "medium"
    return "weak"


def rank_poi(req, poi, origin, anchors):
    score = 0
    tags = poi.get("tags", {})
    name = (poi.get("name") or tags.get("name") or "").lower()
    cuisine = (tags.get("cuisine") or "").lower()
    shop = (tags.get("shop") or "").lower()
    amenity = (tags.get("amenity") or "").lower()
    category = normalize_category(req.get("category", "restaurant"))

    dist = poi.get("distance_m")
    if dist is None:
        dist = 99999
    if dist <= 500:
        score += 30
    elif dist <= 1200:
        score += 20
    elif dist <= 2500:
        score += 10

    if amenity == "cafe":
        score += 18
    if amenity == "restaurant":
        score += 18
    if shop in {"pastry", "confectionery", "bakery"}:
        score += 18
    if re.search(r"ice_cream|dessert|coffee", cuisine, re.I):
        score += 18

    if category == "dessert" and re.search(r"gelato|ice cream|yogurt|dessert|甜|冰|咖啡", name, re.I):
        score += 20
    if category == "restaurant" and re.search(r"餐|饭|锅|烧烤|面|馆|小吃|饺|包子|串|麻辣烫", name, re.I):
        score += 20
    if category == "restaurant" and re.search(r"中餐|火锅|烧烤|西餐|日餐|快餐|特色|地方风味", cuisine, re.I):
        score += 15
    if category == "tea" and re.search(r"茶|奶茶|饮品|果茶", name + " " + cuisine, re.I):
        score += 18
    if category == "bakery" and re.search(r"面包|蛋糕|烘焙|吐司", name + " " + cuisine, re.I):
        score += 18

    if "mall" in req.get("constraints", []) and re.search(r"广场|中心|mall|购物|时代", name, re.I):
        score += 10
    if "seating" in req.get("constraints", []) and str(tags.get("seating", "")).lower() in {"yes", "designated"}:
        score += 8

    text = " ".join([name, cuisine, shop, amenity])
    if "night_market" in req.get("avoid", []) and re.search(r"夜市|market", text, re.I):
        score -= 30
    if "takeaway_only" in req.get("avoid", []) and re.search(r"外带|窗口|档口", text, re.I):
        score -= 20

    return max(0, min(100, score))


def _normalize_text_for_match(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", (text or "").lower())


def _has_store_name_evidence(store_name, candidate_lines):
    target = _normalize_text_for_match(store_name)
    if not target:
        return False
    for line in candidate_lines:
        normalized = _normalize_text_for_match(line)
        if target in normalized:
            return True
    return False


def enrich_poi_with_web(req, pois, verify_limit=2, deadline=None):
    poi_list = []
    for idx, poi in enumerate(pois):
        p = dict(poi)
        p["_base_score"] = p.get("total_score", p.get("score", 0))
        poi_list.append(p)

    if verify_limit > 0:
        print(f"   ⏳ Web enrichment：并行搜 {min(verify_limit, len(poi_list))} 条 POI...", file=sys.stderr, flush=True)
    web_futures = {}
    with ThreadPoolExecutor(max_workers=min(verify_limit, 5)) as executor:
        for p in poi_list[:verify_limit]:
            q = f"{p['name']} {req['origin']} 评价 环境 人均"
            future = executor.submit(run_unified_search, q, deadline)
            web_futures[future] = (p, q)

        for future in as_completed(web_futures):
            p, q = web_futures[future]
            run = future.result()
            lines = _candidate_lines_from_run(run) if run.get("ok") else []
            if not run.get("ok"):
                p["web_query"] = q
                p["web_score"] = None
                p["web_has_store_name"] = False
                p["web_lines"] = []
                p["evidence_level"] = "none"
                p["web_status"] = "failed"
                p["web_source_errors"] = run.get("source_errors", [])
                p["total_score"] = int(p["_base_score"])
                print(f"   ⚠️ {p['name']} web search failed", file=sys.stderr, flush=True)
                continue
            if run.get("count") == 0:
                p["web_query"] = q
                p["web_score"] = None
                p["web_has_store_name"] = False
                p["web_lines"] = []
                p["evidence_level"] = "none"
                p["web_status"] = "partial" if run.get("partial") else "empty"
                p["web_source_errors"] = run.get("source_errors", [])
                p["total_score"] = int(p["_base_score"])
                continue
            web_score = score_evidence(req, lines)
            has_store_name = _has_store_name_evidence(p.get("name"), lines)
            if not has_store_name:
                web_score = min(web_score, 35)
            p["web_query"] = q
            p["web_score"] = web_score
            p["web_has_store_name"] = has_store_name
            p["web_lines"] = lines[:6]
            p["evidence_level"] = _evidence_level(web_score)
            p["web_status"] = (
                "partial" if run.get("partial") else
                "success"
            )
            p["web_source_errors"] = run.get("source_errors", [])
            # Absence of a matching store name is uncertainty, not negative
            # evidence; retain the structured POI score and withhold verification.
            p["total_score"] = (
                int(p["_base_score"] * 0.75 + web_score * 0.25)
                if has_store_name else int(p["_base_score"])
            )
            print(f"   ✅ {p['name']} web_score={web_score}", file=sys.stderr, flush=True)

    for p in poi_list[verify_limit:]:
        p["web_query"] = None
        p["web_score"] = None
        p["web_lines"] = []
        p["evidence_level"] = "none"
        p["web_status"] = "not_checked"
        p["total_score"] = int(p["_base_score"])

    poi_list.sort(key=lambda x: (-x.get("total_score", 0), x.get("distance_m", 99999)))
    for p in poi_list:
        p.pop("_base_score", None)
    return poi_list


def _build_evidence_bundles(req, search_runs):
    bundles = []
    for run in search_runs:
        lines = _candidate_lines_from_run(run) if run.get("ok") else []
        score = score_evidence(req, lines) if run.get("ok") else 0
        bundles.append({
            "query": run["query"],
            "score": score,
            "lines": lines,
            "ok": run["ok"],
        })
    bundles.sort(key=lambda x: x["score"], reverse=True)
    return bundles


def _execution_status(poi_result, search_runs, enriched_pois=None):
    """Summarize component execution without changing the legacy result.status field."""
    poi_results = poi_result.get("results", []) or []
    if poi_result.get("error"):
        poi_status = "error"
    elif poi_results:
        poi_status = "partial" if poi_result.get("fallback_from") else "success"
    else:
        poi_status = "empty"

    web_states = []
    for run in search_runs:
        if not run.get("ok"):
            web_states.append("error")
        elif run.get("partial"):
            web_states.append("partial")
        elif run.get("count") == 0:
            web_states.append("empty")
        else:
            web_states.append("success")
    for poi in enriched_pois or []:
        state = poi.get("web_status")
        if state in {"failed", "partial", "empty", "success"}:
            web_states.append("error" if state == "failed" else state)

    if not web_states:
        web_status = "not_run"
    elif all(state == "empty" for state in web_states):
        web_status = "empty"
    elif all(state == "error" for state in web_states):
        web_status = "error"
    elif any(state in {"error", "partial"} for state in web_states):
        web_status = "partial"
    else:
        web_status = "success"

    if poi_status in {"success", "partial"}:
        overall = "partial" if poi_status == "partial" or web_status in {"error", "partial"} else "success"
    elif poi_status == "empty":
        overall = "empty" if web_status in {"not_run", "empty"} else "partial"
    else:
        overall = "partial" if web_status in {"success", "empty", "partial"} else "error"

    return {
        "overall": overall,
        "components": {"poi": poi_status, "web_search": web_status},
    }


def _assess_bundle_quality(bundles, req):
    """Assess whether unified-search evidence is sufficient."""
    if not bundles:
        return {"quality": "poor", "need_fallback": True, "reasons": ["no_evidence"]}

    scores = [b["score"] for b in bundles]
    max_score = max(scores) if scores else 0
    avg_score = sum(scores) / len(scores) if scores else 0

    reasons = []
    need_fallback = False

    zero_score_queries = [b for b in bundles if b["score"] <= 2]
    if zero_score_queries:
        reasons.append(f"{len(zero_score_queries)} queries with score<=2")
        need_fallback = True

    if max_score <= 2:
        reasons.append(f"max evidence score only {max_score}")
        need_fallback = True

    if avg_score < 10:
        reasons.append(f"avg evidence score {avg_score:.1f} < 10")
        need_fallback = True

    if not need_fallback:
        return {"quality": "acceptable", "need_fallback": False, "reasons": []}

    return {
        "quality": "poor" if max_score <= 2 else "weak",
        "need_fallback": need_fallback,
        "reasons": reasons,
    }


def _specialty_fallback_run(req):
    """Run specialty provider fallback. Returns a pseudo-run dict or None."""
    from providers import search_maoyan_cinema_halls, SPECIAL_HALL_KEYWORDS

    category = req.get("category", "")
    if category != "电影院":
        return None

    origin = req.get("origin", "")
    city_match = re.search(
        r'(天津|北京|上海|广州|深圳|成都|杭州|南京|武汉|西安|重庆|长沙|苏州|郑州|厦门|福州|青岛|大连|沈阳|昆明)',
        origin
    )
    if not city_match:
        return None
    city_name = city_match.group(1)

    query_text = req.get("query", "")
    special_types = []
    for hall_key in SPECIAL_HALL_KEYWORDS:
        if any(kw.lower() in query_text.lower() for kw in SPECIAL_HALL_KEYWORDS[hall_key]):
            special_types.append(hall_key)

    if not special_types:
        special_types = ["4DX", "D-BOX", "IMAX", "杜比"]

    try:
        result = search_maoyan_cinema_halls(city_name, special_types, max_cinemas=80)
        cinemas = result.get("cinemas", [])
    except Exception:
        return None

    if not cinemas:
        return None

    lines = []
    lines.append(f"Title: 猫眼专项查询 {city_name} 电影院 {' '.join(special_types)}")
    lines.append(f"URL: https://m.maoyan.com/cinemas?cityName={urllib.parse.quote(city_name)}")
    for c in cinemas:
        hall_str = ", ".join(c.get("hall_types", []))
        addr = c.get("address", "")
        lines.append(f"Title: {c['name']} ({hall_str})")
        if addr:
            lines.append(f"{c['name']} {addr} 影厅: {hall_str} 电影院 看电影 影院 {city_name}")

    return {
        "query": f"猫眼专项: {origin} 电影院",
        "ok": True,
        "output": "\n".join(lines),
        "provider": "maoyan",
    }


def _confidence_band(results):
    if not results:
        return "low"
    top = results[0].get("total_score", results[0].get("score", 0))
    top_has_evidence = bool(results[0].get("web_has_store_name") and results[0].get("web_score") is not None)
    if len(results) >= 5 and top >= 75 and top_has_evidence:
        return "high"
    if top >= 55 and top_has_evidence:
        return "medium"
    return "low"


def decide_search(req, poi_result, search_runs, enriched_pois):
    bundles = _build_evidence_bundles(req, search_runs)
    anchors = expand_anchors(req["origin"])
    results = enriched_pois[:SEARCH_DISPLAY_LIMIT]

    if not results:
        return {
            "status": "ok",
            "mode": "search",
            "resolution_mode": "area_fallback",
            "confidence_band": "low",
            "summary": "结构化 POI 未返回稳定结果，当前只能回退到区域级线索。",
            "top_candidates": anchors[:2],
            "results": [],
            "evidence": bundles[:2],
        }

    top_candidates = [p["name"] for p in results[:3]]
    checked_count = sum(1 for p in results if p.get("web_status") != "not_checked")
    summary = f"共找到 {len(results)} 个候选，优先看 {', '.join(top_candidates)}。已尝试对其中 {checked_count} 个候选做网页补充检索；店名匹配状态见候选字段。"
    return {
        "status": "ok",
        "mode": "search",
        "resolution_mode": "list",
        "confidence_band": _confidence_band(results),
        "summary": summary,
        "top_candidates": top_candidates,
        "results": results,
        "evidence": bundles[:2],
    }


def decide_recommend(req, poi_result, search_runs, enriched_pois):
    bundles = _build_evidence_bundles(req, search_runs)
    anchors = expand_anchors(req["origin"])

    verified = [
        p for p in enriched_pois
        if p.get("web_has_store_name") and p.get("web_score") is not None
    ]
    strong_verified = [p for p in verified if p.get("web_score", 0) >= 45]

    if strong_verified:
        top = strong_verified[0]
        backups = [p["name"] for p in strong_verified[1:3]]
        if not backups:
            backups = [p["name"] for p in enriched_pois if p["name"] != top["name"]][:2]
        return {
            "status": "ok",
            "mode": "recommend",
            "resolution_mode": "store_level",
            "confidence": "high" if top.get("web_score", 0) >= 70 and top.get("total_score", 0) >= 75 else "medium",
            "top_pick": top["name"],
            "top_area": req["origin"],
            "backups": backups,
            "reason": "先由结构化 POI 主源召回；候选名称在网页结果中命中，且类别相关证据达到当前阈值。",
            "poi_candidates": enriched_pois[:RECOMMEND_DISPLAY_LIMIT],
            "evidence": bundles[:2],
        }

    top_web = bundles[0] if bundles else None
    if not top_web or top_web["score"] < 45:
        return {
            "status": "ok",
            "mode": "recommend",
            "resolution_mode": "area_fallback",
            "confidence": "low",
            "top_pick": anchors[1] if len(anchors) > 1 else anchors[0],
            "backups": anchors[2:4],
            "reason": "当前店级网页证据不足，已降级为区域/锚点级推荐，避免幻觉式拍板店名。",
            "poi_candidates": enriched_pois[:RECOMMEND_DISPLAY_LIMIT],
            "evidence": bundles[:2],
        }

    return {
        "status": "ok",
        "mode": "recommend",
        "resolution_mode": "area_or_store",
        "confidence": "medium",
        "top_pick": anchors[1] if len(anchors) > 1 else anchors[0],
        "backups": [p["name"] for p in enriched_pois[:2]],
        "reason": "结构化主源已有候选，但网页证据仍不足以稳定拍板具体店名，因此维持区域级建议。",
        "poi_candidates": enriched_pois[:RECOMMEND_DISPLAY_LIMIT],
        "evidence": bundles[:2],
    }


def _attach_transit_details(poi_result, pois, limit=2):
    if not pois:
        return pois

    origin = poi_result.get("origin") or {}
    origin_lng = origin.get("lon")
    origin_lat = origin.get("lat")
    if origin_lng is None or origin_lat is None:
        return pois

    try:
        import amap_direction
    except Exception:
        return pois

    attached = 0
    for poi in pois:
        if attached >= limit:
            break

        accessibility = poi.get("accessibility") or {}
        if accessibility.get("mode") != "transit":
            continue

        dest_lng = poi.get("lon")
        dest_lat = poi.get("lat")
        if dest_lng is None or dest_lat is None:
            continue

        try:
            details = amap_direction.get_transit_details(origin_lng, origin_lat, dest_lng, dest_lat)
            if not details:
                continue
            poi["transit_details"] = details
            poi["transit_detail_lines"] = amap_direction.format_transit_detail_lines(details)
            attached += 1
        except Exception:
            continue

    return pois


def _accessibility_label(poi):
    accessibility = poi.get("accessibility") or {}
    duration_min = accessibility.get("duration_min")
    mode = accessibility.get("mode", "")
    if duration_min is None:
        return ""
    if mode == "walking":
        if duration_min <= 15:
            return "✅ 步行可达"
        elif duration_min <= 30:
            return "⚠️ 步行较远"
        else:
            return "❌ 步行不便"
    if mode == "transit":
        if duration_min <= 30:
            return "✅ 公共交通可达"
        elif duration_min <= 45:
            return "⚠️ 公共交通耗时较长"
        return "❌ 公共交通不便"
    if mode == "estimate":
        return "📍 步行时间为直线距离估算"
    if mode == "error":
        return "⚠️ 暂无可达性数据"
    return ""


def _constraint_assessments(req, poi):
    tags = poi.get("tags") or {}
    if not isinstance(tags, dict):
        tags = {}
    assessments = {}
    for raw_constraint in req.get("constraints", []):
        constraint = raw_constraint
        for key, hints in CONSTRAINT_HINTS.items():
            if raw_constraint == key or raw_constraint in hints:
                constraint = key
                break

        state = "unknown"
        if constraint == "seating":
            seating = str(tags.get("seating") or "").strip().lower()
            if seating in {"yes", "designated"}:
                state = "met"
            elif seating == "no":
                state = "not_met"
        elif constraint == "metro":
            transit_parts = [
                part
                for step in (poi.get("transit_details") or {}).get("steps", [])
                for part in step.get("parts", [])
                if part.get("type") == "bus"
            ]
            if any(part.get("is_subway") for part in transit_parts):
                state = "met"
            elif transit_parts:
                state = "not_met"
        assessments[str(raw_constraint)] = state
    return assessments


def _render_poi_line(poi):
    parts = [
        poi["name"],
        f"score={poi.get('total_score', poi.get('score'))}",
        f"distance={poi.get('distance_m', '?')}m",
    ]
    accessibility = poi.get("accessibility") or {}
    if accessibility.get("display"):
        parts.append(f"access={accessibility['display']}")
    label = _accessibility_label(poi)
    if label:
        parts.append(label)
    assessments = poi.get("constraint_assessments") or {}
    if assessments:
        labels = {"met": "已确认", "not_met": "未满足", "unknown": "未确认"}
        parts.append("constraints=" + ", ".join(
            f"{key}:{labels.get(value, value)}" for key, value in assessments.items()
        ))
    if poi.get("evidence_level"):
        parts.append(f"evidence={poi['evidence_level']}")
    return "- " + " | ".join(parts)


def render_markdown(req, result, poi_result):
    lines = []
    lines.append("# Local POI Planner")
    lines.append("")
    lines.append(f"- 模式：{req['mode']}")
    lines.append(f"- 起点：{req['origin']}")
    lines.append(f"- 半径：{req.get('radius_m', 3000)}m")
    lines.append(f"- 类别：{req['category']}")
    lines.append(f"- 偏好：{', '.join(req['preferences']) or '未指定'}")
    lines.append(f"- 约束：{', '.join(req['constraints']) or '未指定'}")
    lines.append(f"- 回避：{', '.join(req['avoid']) or '未指定'}")
    lines.append(f"- provider: {poi_result.get('provider', 'unknown')}")
    lines.append(f"- origin resolved: {((poi_result.get('origin') or {}).get('display_name')) or '未解析'}")
    if result.get("mode") == "search":
        lines.append(f"- 置信带：{result['confidence_band']}")
        lines.append("")
        lines.append("## 概览")
        lines.append(f"- {result['summary']}")
        lines.append("")
        lines.append("## 优先看")
        for name in result.get("top_candidates", []):
            lines.append(f"- {name}")
        lines.append("")
        lines.append("## 候选列表")
        for poi in result.get("results", []):
            lines.append(_render_poi_line(poi))
            detail_lines = poi.get("transit_detail_lines") or []
            if detail_lines:
                lines.append("  - transit detail:")
                for item in detail_lines:
                    lines.append(f"    - {item}")
    else:
        lines.append(f"- 置信度：{result['confidence']}")
        lines.append("")
        lines.append("## 首选")
        lines.append(f"- 推荐：**{result['top_pick']}**")
        lines.append(f"- 理由：{result['reason']}")
        lines.append("")
        lines.append("## 结构化候选")
        for poi in result.get("poi_candidates", []):
            lines.append(_render_poi_line(poi))
            detail_lines = poi.get("transit_detail_lines") or []
            if detail_lines:
                lines.append("  - transit detail:")
                for item in detail_lines:
                    lines.append(f"    - {item}")
        lines.append("")
        lines.append("## 备选")
        if result.get("backups"):
            for x in result["backups"]:
                lines.append(f"- {x}")
        else:
            lines.append("- 暂无明确备选")
        lines.append("")
        lines.append("## 风险点")
        if result.get("resolution_mode") == "area_fallback":
            lines.append("- 当前是区域级/锚点级结论，不是稳定的店名级拍板")
            lines.append("- 如果要最终拍板，建议到场后二次筛店")
        else:
            lines.append("- 当前店级结果仍受网页索引质量与 POI 标注质量影响")
    lines.append("")
    lines.append("## 网页证据摘要")
    for ev in result.get("evidence", []):
        lines.append(f"### Query: {ev['query']}")
        lines.append(f"- score: {ev['score']}")
        for line in ev["lines"][:5]:
            lines.append(f"- {line}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="Natural language request")
    ap.add_argument("--origin")
    ap.add_argument("--category")
    ap.add_argument("--mode", choices=["search", "recommend"])
    ap.add_argument("--preferences")
    ap.add_argument("--constraints")
    ap.add_argument("--avoid")
    ap.add_argument("--format", choices=["json", "markdown"], default="json")
    ap.add_argument("--corelocation", action="store_true", help="Force CoreLocationCLI for current position")
    ap.add_argument("--timeout", type=float, default=1800, help="Overall request budget in seconds (default: 1800)")
    ap.add_argument("--location-policy", choices=["auto", "corelocation", "ip", "disabled"], default="auto",
                    help="Location lookup for nearby requests (default: auto; disabled requires --origin)")
    args = ap.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        ap.error("--timeout must be a finite positive number")
    t0 = time.time()
    deadline = time.monotonic() + args.timeout
    set_request_deadline(deadline)

    # --corelocation: resolve current position via CoreLocationCLI
    if args.corelocation:
        try:
            from macos_location import get_macos_location
            loc = get_macos_location(timeout=min(10, max(0.01, deadline - time.monotonic())))
            if loc:
                args.origin = f"{loc['lat']},{loc['lon']}"
                print(f"📍 CoreLocation: {loc['lat']:.5f}, {loc['lon']:.5f}", file=sys.stderr, flush=True)
            else:
                print("⚠️ CoreLocation returned no coordinates, falling back", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"⚠️ CoreLocation failed ({e}), falling back", file=sys.stderr, flush=True)

    print("⏳ 解析请求...", file=sys.stderr, flush=True)

    req = parse_request(args, deadline=deadline)
    provider_limit = SEARCH_PROVIDER_LIMIT if req["mode"] == "search" else RECOMMEND_PROVIDER_LIMIT
    verify_limit = SEARCH_WEB_VERIFY_LIMIT if req["mode"] == "search" else RECOMMEND_WEB_VERIFY_LIMIT
    transit_attach_limit = 4 if req["mode"] == "search" else 2

    print(f"⏳ POI 搜索（{req['origin']}，半径 {req.get('radius_m', 3000)}m）...", file=sys.stderr, flush=True)
    poi_result = search_pois(req, req["origin"], radius_m=req.get("radius_m", 3000), limit=provider_limit, ip_location=req.get("ip_location"))
    poi_candidates = poi_result.get("results", [])
    print(f"   ✅ POI 搜索：{len(poi_candidates)} 条候选（{time.time()-t0:.0f}s）", file=sys.stderr, flush=True)
    if poi_result.get("error") == "origin_required":
        enriched_pois = []
        queries = []
        runs = []
        print("⚠️ 缺少起点；跳过联网搜索，请提供 --origin 或启用位置解析", file=sys.stderr, flush=True)
    else:
        print(f"⏳ Web enrichment（最多 {verify_limit} 条 POI）...", file=sys.stderr, flush=True)
        enriched_pois = enrich_poi_with_web(req, poi_candidates, verify_limit=verify_limit, deadline=deadline)
        queries = build_queries(req, expand_anchors(req["origin"]), enriched_pois, mode=req["mode"], deadline=deadline)
        print(f"   ✅ 查询构建：{len(queries)} 条（{time.time()-t0:.0f}s）", file=sys.stderr, flush=True)
        print(f"⏳ Unified-search（串行，共 {len(queries)} 条）...", file=sys.stderr, flush=True)
        runs = [run_unified_search(q, deadline) for q in queries]

    print(f"   ✅ Unified-search 完成（{time.time()-t0:.0f}s）", file=sys.stderr, flush=True)
    # Quality gate: trigger specialty fallback when evidence is weak
    pre_bundles = _build_evidence_bundles(req, runs)
    quality = _assess_bundle_quality(pre_bundles, req)
    if quality["need_fallback"]:
        print(f"   ⚠️ Evidence {quality['quality']} ({'; '.join(quality['reasons'])}), specialty fallback...", file=sys.stderr, flush=True)
        fb = _specialty_fallback_run(req)
        if fb:
            runs.insert(0, fb)
    print("⏳ 决策 + 交通详情...", file=sys.stderr, flush=True)
    if req["mode"] == "search":
        result = decide_search(req, poi_result, runs, enriched_pois)
        result["results"] = _attach_transit_details(
            poi_result,
            result.get("results", []),
            limit=transit_attach_limit,
        )
    else:
        result = decide_recommend(req, poi_result, runs, enriched_pois)
        result["poi_candidates"] = _attach_transit_details(
            poi_result,
            result.get("poi_candidates", []),
            limit=transit_attach_limit,
        )

    output_candidates = result.get("results", []) or result.get("poi_candidates", []) or []
    for poi in output_candidates:
        poi["constraint_assessments"] = _constraint_assessments(req, poi)

    request_output = dict(req)
    if isinstance(request_output.get("ip_location"), dict):
        request_output["ip_location"] = {
            key: value
            for key, value in request_output["ip_location"].items()
            if key != "source_ip"
        }
    payload = {
        "status": _execution_status(poi_result, runs, enriched_pois),
        "request": request_output,
        "poi_provider": poi_result,
        "queries": queries,
        "result": result,
    }
    print(f"   ✅ 完成（总耗时 {time.time()-t0:.0f}s）", file=sys.stderr, flush=True)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(req, result, poi_result))


if __name__ == "__main__":
    main()
