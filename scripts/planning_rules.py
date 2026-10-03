"""Deterministic request rules and evidence-backed candidate assessments.

Unknown business data stays unknown. Search snippets never prove hard conditions.
"""
from datetime import datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo

NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)"
ALIASES = {
    "seating": ["有座位", "堂食", "能坐着", "坐着吃", "坐着聊", "座位"],
    "metro": ["地铁可达"],
    "near_metro": ["近地铁", "地铁站附近"],
    "mall": ["商场内", "商场", "购物中心"],
    "quiet": ["清静", "安静"],
    "environment": ["环境好", "适合约会", "适合聊天"],
}
PREFERENCE_TERMS = {
    **ALIASES,
    "yogurt": ["酸奶", "yogurt"],
    "gelato": ["gelato", "手工冰淇淋", "意式冰淇淋"],
    "smoothie": ["冰沙", "果昔", "smoothie"],
    "light": ["清爽", "不腻", "轻食"],
}
LABELS = {"seating": "座位", "metro": "地铁路线", "near_metro": "距地铁站500米内",
          "mall": "商场内", "quiet": "安静", "environment": "环境",
          "budget": "人均预算", "open_at": "营业时间", "radius": "距离范围"}


def number(text):
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    digits = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    value = current = 0
    for char in text:
        if char in digits:
            current = digits[char]
        elif char in "十百千":
            value += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
        else:
            raise ValueError("Unsupported number")
    return float(value + current)


def canonical(value):
    value = value.strip().lower()
    for key, aliases in PREFERENCE_TERMS.items():
        if value == key or value in aliases:
            return key
    return value


def csv(value):
    return [canonical(x) for x in re.split(r"[,，]", value or "") if x.strip()]


def parse_radius(text, default=3000):
    match = re.search(rf"({NUMBER})\s*(km|公里|千米|m|米)(?![a-zA-Z])", text or "", re.I)
    if not match:
        return default
    return int(number(match[1]) * (1000 if match[2].lower() in {"km", "公里", "千米"} else 1))


def parse_iso_datetime(value):
    # Python 3.10 fromisoformat does not accept the ISO UTC suffix Z.
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def parse_requirements(args, query, now=None):
    timezone = getattr(args, "timezone", "Asia/Shanghai")
    tz = ZoneInfo(timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    hard = {c for c in csv(getattr(args, "constraints", None)) + csv(getattr(args, "must", None))
            if parse_radius(c, None) is None}
    soft = set(csv(getattr(args, "preferences", None)))
    for clause in re.split(r"[，,。；;]|但是|而且|并且|但", query):
        for key, aliases in PREFERENCE_TERMS.items():
            for term in aliases:
                match = re.search(re.escape(term), clause, re.I)
                if not match:
                    continue
                prefix = clause[:match.start()]
                if re.search(r"(?:不要|不需要|无需|不要求|不想|不喜欢|避开|没有|无)[^，,]{0,8}$", prefix):
                    continue
                directives = re.findall(r"必须|一定|务必|优先|最好|尽量|偏好|希望", prefix)
                directive = directives[-1] if directives else None
                is_soft = directive in {"优先", "最好", "尽量", "偏好", "希望"}
                is_hard = directive in {"必须", "一定", "务必"} or (key in {"seating", "metro", "near_metro", "mall"} and not is_soft)
                (hard if is_hard else soft).add(key)
    # An explicit must wins over a preference, while absent attributes stay unknown.
    soft -= hard
    budget = getattr(args, "budget_max", None)
    warnings = []
    budget_unresolved = False
    if budget is None:
        match = re.search(rf"人均\s*(?:不超过|最多|不高于|小于等于|[≤<=]+)?\s*({NUMBER})\s*(?:元|块)?", query)
        if match:
            if re.match(r"\s*(?:以上|起|到|至|[-~]|美元|美金|欧元|日元)", query[match.end():]):
                budget_unresolved = True
            else:
                budget = number(match[1])
        elif "人均" in query:
            budget_unresolved = True
    if budget_unresolved:
        warnings.append("未能确定人民币人均上限；请用 --budget-max 指定。")
    if budget is not None and (not math.isfinite(budget) or budget <= 0):
        raise ValueError("--budget-max must be a finite positive amount per person")
    stay = getattr(args, "stay_minutes", None)
    stay_unresolved = False
    if stay is None:
        match = re.search(rf"(?:坐|待|停留|用餐)\s*(?:至少|满)?\s*(半|{NUMBER})\s*个?\s*(半)?\s*(小时|分钟)", query)
        if match:
            amount = (0.5 if match[1] == "半" else number(match[1])) + (0.5 if match[2] else 0)
            stay = math.ceil(amount * (60 if match[3] == "小时" else 1))
        else:
            stay = 0
            stay_unresolved = bool(re.search(r"(?:坐|待|停留|用餐)[^，,。；;]{0,8}(?:小时|分钟|一会|一阵|整晚)", query))
            if stay_unresolved:
                warnings.append("停留时长未能确定；请用 --stay-minutes 指定。")
    if not 0 <= stay <= 1440:
        raise ValueError("--stay-minutes must be between 0 and 1440")
    visit_arg = getattr(args, "visit_at", None)
    visit = None
    time_unresolved = False
    if visit_arg:
        visit = parse_iso_datetime(visit_arg)
        visit = visit.replace(tzinfo=tz) if visit.tzinfo is None else visit.astimezone(tz)
    else:
        # Require a date/period or a clause/time cue, so shop names such as
        # “一点点奶茶” do not invent an arrival time.
        match = re.search(rf"(?:(今天|今晚|明天|明晚|后天)\s*(上午|早上|中午|下午|晚上)?|(上午|早上|中午|下午|晚上)|(?:^|[，,。；;]|在|于|约)\s*)\s*({NUMBER})\s*(?:[:：](\d{{2}})|点(?:(半)|({NUMBER})分?)?)(?![点\d零〇一二两三四五六七八九十百千])", query)
        if match and (re.search(r"周[一二三四五六日天末]|星期|下周|\d{4}[-年]|\d+月|大后天", query) or query[match.end():].startswith("刻")):
            time_unresolved = True
        elif match:
            hour = int(number(match[4]))
            minute = int(match[5] or (30 if match[6] else number(match[7]) if match[7] else 0))
            period = (match[1] or "") + (match[2] or "") + (match[3] or "")
            if any(x in period for x in ("晚", "下午")) and hour < 12:
                hour += 12
            elif "中午" in period and hour < 11:
                hour += 12
            elif any(x in period for x in ("上午", "早上")) and hour == 12:
                hour = 0
            offset = 2 if match[1] == "后天" else 1 if match[1] in {"明天", "明晚"} else 0
            visit = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif any(x in query for x in ("今晚", "明天", "明晚", "后天", "周末", "营业")) and not any(x in query for x in ("现在", "正在营业", "营业中")):
            time_unresolved = True
        elif any(x in query for x in ("现在", "正在营业", "营业中")) or stay:
            visit = now
    if time_unresolved:
        warnings.append("到访时间未能确定；请用 --visit-at 指定当地日期和时间。")
    return {"constraints": sorted(hard), "preferences": sorted(soft), "budget_max": budget,
            "visit_at": visit.isoformat() if visit else None, "stay_minutes": stay,
            "timezone": timezone, "evaluated_at": now.isoformat(), "request_warnings": warnings,
            "time_unresolved": time_unresolved, "budget_unresolved": budget_unresolved,
            "stay_unresolved": stay_unresolved}


def numeric(value):
    if isinstance(value, bool) or value in (None, "", []):
        return None
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (TypeError, ValueError):
        return None


def _intervals(text):
    """Parse only unambiguous daily intervals; never guess complex weekly rules."""
    text = str(text or "").strip()
    if text in {"24/7", "24小时", "24小时营业", "全天", "00:00-24:00"}:
        return [(0, 1440)]
    if text.lower() in {"off", "closed", "休息", "暂停营业", "歇业"}:
        return []
    pattern = r"(\d{1,2}):(\d{2})\s*[-~至–—]\s*(\d{1,2}):(\d{2})"
    matches = list(re.finditer(pattern, text))
    remainder = re.sub(pattern, "", text)
    if not matches or re.sub(r"[\s,，;；、]", "", remainder):
        return None
    result = []
    for m in matches:
        h1, m1, h2, m2 = map(int, m.groups())
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59 or (h2 == 24 and m2):
            return None
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if end == start:
            return None
        if end < start:
            end += 1440
        result.append((start, end))
    merged = []
    for start, end in sorted(result):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def opening_check(req, poi):
    business = poi.get("business") or {}
    visit = parse_iso_datetime(req["visit_at"]).astimezone(ZoneInfo(req.get("timezone", "Asia/Shanghai")))
    observed = business.get("observed_at") or poi.get("observed_at")
    today = business.get("opentime_today")
    # Provider "today" hours cannot establish tomorrow's schedule or yesterday's carry-over.
    if today and observed:
        try:
            observed_date = parse_iso_datetime(observed).astimezone(visit.tzinfo).date()
        except ValueError:
            observed_date = None
        if visit.date() != observed_date:
            return "unknown", "仅有抓取当天营业表，不能确认到访日期"
        intervals = _intervals(today)
        carry_over = False
    else:
        text = (poi.get("tags") or {}).get("opening_hours")
        intervals = _intervals(text)
        carry_over = True  # A bare OSM interval describes the same schedule every day.
    if intervals is None:
        return "unknown", "营业表缺失或含未支持的复杂时间规则"
    minute = visit.hour * 60 + visit.minute + visit.second / 60
    end = minute + req.get("stay_minutes", 0)
    # Repeating daily schedules may continue across midnight (including 24/7).
    spans = sorted((start + shift, close + shift) for start, close in intervals
                   for shift in ((-1440, 0, 1440) if carry_over else (0,)))
    merged = []
    for start, close in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(close, merged[-1][1]))
        else:
            merged.append((start, close))
    for start, close in merged:
        if start <= minute < close and end <= close:
            return "met", "营业表覆盖到访及停留时段（非实时营业确认）"
        if not carry_over and start <= minute < close == 1440 and end > close:
            return "unknown", "停留跨入下一天，缺少下一日营业表"
    if intervals and not carry_over and minute < 6 * 60:
        return "unknown", "缺少前一日跨午夜营业表，无法确认凌晨时段"
    return "not_met", "营业表未覆盖到访或完整停留时段"


def attribute_state(poi, key):
    tags = poi.get("tags") or {}
    value = str(tags.get(key, "")).lower()
    if key == "seating":
        value = str(tags.get("seating", tags.get("indoor_seating", ""))).lower()
        if str(tags.get("takeaway", "")).lower() == "only":
            value = "no"
    if value in {"yes", "designated", "true"}:
        return "met"
    if value in {"no", "false"}:
        return "not_met"
    if key == "metro":
        has_subway = (poi.get("accessibility") or {}).get("has_subway")
        if isinstance(has_subway, bool):
            # A bus-only fastest route does not prove there is no subway option.
            return "met" if has_subway else "unknown"
        parts = [part for step in (poi.get("transit_details") or {}).get("steps", []) for part in step.get("parts", []) if part.get("type") == "bus"]
        if parts:
            return "met" if any(p.get("is_subway") for p in parts) else "unknown"
    if key == "near_metro":
        distance = numeric(poi.get("nearest_metro_distance_m"))
        if distance is not None:
            return "met" if distance <= 500 else "not_met"
    return "unknown"


def preference_matches(req, poi):
    business = poi.get("business") or {}
    tags = poi.get("tags") or {}
    # Name and structured tags are tied to this POI. Unbound web snippets are excluded.
    text = " ".join(str(x) for x in [poi.get("name", ""), business.get("tag", ""), tags.get("cuisine", ""), tags.get("description", "")])
    matched = []
    for pref in req.get("preferences", []):
        pref = canonical(pref)
        state = attribute_state(poi, pref)
        if state == "not_met":
            continue
        if state == "met":
            matched.append(pref)
            continue
        for term in PREFERENCE_TERMS.get(pref, [pref]):
            match = re.search(re.escape(term), text, re.I)
            if match and not re.search(r"(?:不|无|没有|非|not\s|no\s).{0,3}$", text[:match.start()], re.I):
                matched.append(pref)
                break
    return matched


def assess_candidate(req, candidate):
    poi = dict(candidate)
    checks = {}
    source = poi.get("data_source") or poi.get("provider", "unknown")
    observed = poi.get("observed_at")

    def add(key, state, reason):
        checks[key] = {"state": state, "reason": reason, "source": source, "observed_at": observed}

    for raw in req.get("constraints", []):
        key = canonical(raw)
        state = attribute_state(poi, key)
        add(key, state, f"{LABELS.get(key, key)}：" + {"met": "结构化信息支持", "not_met": "结构化信息不符", "unknown": "缺少可核验证据"}[state])
        if key == "metro" and (poi.get("accessibility") or {}).get("has_subway") is not None and not (poi.get("tags") or {}).get("metro"):
            evidence = poi["accessibility"]
            checks[key].update(source=evidence.get("data_source", "amap_direction"), observed_at=evidence.get("observed_at"))
            checks[key]["reason"] = "已返回含地铁的路线" if state == "met" else "已返回路线未含地铁，其他路线尚未核实"
    distance = numeric(poi.get("distance_m"))
    if distance is not None and distance < 0:
        distance = None
    if req.get("radius_m") is not None:
        add("radius", "unknown" if distance is None else "met" if distance <= req["radius_m"] else "not_met", "按直线/地图检索距离判断，不代表步行路程")
    business = poi.get("business") or {}
    cost = numeric(business.get("cost"))
    if req.get("budget_max") is not None:
        valid_cost = cost is not None and cost > 0
        state = "unknown" if not valid_cost else "met" if cost <= req["budget_max"] else "not_met"
        add("budget", state, f"参考人均 ¥{cost:g}，预算上限 ¥{req['budget_max']:g}（非报价）" if valid_cost else "缺少有效人均消费数据")
    if req.get("stay_unresolved"):
        add("open_at", "unknown", "停留时长未明确，无法判断完整营业时段")
    elif req.get("visit_at"):
        state, reason = opening_check(req, poi)
        add("open_at", state, reason)
    elif req.get("time_unresolved"):
        add("open_at", "unknown", "到访时间未明确，无法判断营业时段")
    if req.get("budget_unresolved"):
        add("budget", "unknown", "人均预算未明确，无法判断消费范围")
    states = [c["state"] for c in checks.values()]
    poi["eligibility"] = "excluded" if "not_met" in states else "needs_verification" if "unknown" in states else "eligible"
    poi["requirement_checks"] = checks
    poi["constraint_assessments"] = {key: value["state"] for key, value in checks.items()}
    matches = preference_matches(req, poi)
    poi["matched_preferences"] = matches
    poi["preference_score"] = min(24, 8 * len(matches))
    poi["ranking_score"] = round((numeric(poi.get("total_score", poi.get("score"))) or 0) + poi["preference_score"], 1)
    reasons = []
    if distance is not None:
        reasons.append(f"距起点约 {distance:g} 米")
    if matches:
        reasons.append("偏好命中：" + "、".join(matches) + "（店名/商户标签）")
    for key, check in checks.items():
        if key != "radius" or check["state"] != "met":
            reasons.append(check["reason"])
    poi["recommendation_reasons"] = reasons
    return poi


def candidate_sort_key(poi):
    return ({"eligible": 0, "needs_verification": 1, "excluded": 2}.get(poi.get("eligibility"), 1),
            -poi.get("ranking_score", poi.get("total_score", poi.get("score", 0))),
            numeric(poi.get("distance_m")) if numeric(poi.get("distance_m")) is not None else math.inf)


def rank_candidates(req, candidates):
    return sorted((assess_candidate(req, poi) for poi in candidates), key=candidate_sort_key)
