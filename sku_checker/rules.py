"""业务规则：价格、配送、总状态。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

US_TZ = ZoneInfo("America/Los_Angeles")


def us_today(now: datetime | None = None) -> date:
    now = now or datetime.now(tz=US_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=US_TZ)
    return now.astimezone(US_TZ).date()


def parse_delivery_first_day(
    text: str | None,
    year: int,
    *,
    check_date: date | None = None,
) -> date | None:
    """解析配送日期；若为范围取第一天，并处理常见相对日期与跨年。"""
    if not text:
        return None
    base = check_date or date(year, 1, 1)
    s0 = " ".join(text.strip().split())
    # Amazon 原文可能包含前后缀，例如：
    # "Or fastest delivery Saturday, July 25. Order within 2 hrs"。
    # 先提取其中明确的英文日期片段，再沿用统一日期解析。
    embedded = re.search(
        r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+)?"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}"
        r"(?:,?\s+\d{4})?",
        s0,
        re.I,
    )
    if embedded:
        s0 = embedded.group(0)
    relative = s0.lower()
    # Amazon 的 Overnight 表示隔夜送达，按采集当天（美国当地日期）+1 计算。
    # 后面的 "7 AM - 11 AM" 是时间窗口，不是日期范围。
    if re.search(r"\bovernight\b", relative):
        return base + timedelta(days=1)
    if "tomorrow" in relative:
        return base + timedelta(days=1)
    if "today" in relative:
        return base

    for prefix in ("this ", "next "):
        if relative.startswith(prefix):
            weekday_name = relative[len(prefix):].split()[0].rstrip(",")
            weekdays = {
                "monday": 0,
                "tuesday": 1,
                "wednesday": 2,
                "thursday": 3,
                "friday": 4,
                "saturday": 5,
                "sunday": 6,
            }
            if weekday_name in weekdays:
                days = (weekdays[weekday_name] - base.weekday()) % 7
                if prefix == "next " and days == 0:
                    days = 7
                return base + timedelta(days=days)

    for sep in ["–", "—", " - ", " to ", " until "]:
        if sep in s0:
            s0 = s0.split(sep)[0].strip()
            break
    s0 = s0.replace("Arrives", "").replace("Get it", "").strip(" ,")
    for fmt in (
        "%A, %B %d %Y",
        "%A, %B %d, %Y",
        "%B %d %Y",
        "%B %d, %Y",
        "%A, %B %d",
        "%B %d",
    ):
        try:
            if "%Y" not in fmt:
                parsed = datetime.strptime(f"{s0} {year}", f"{fmt} %Y").date()
                # Amazon年末显示下一年1月时通常省略年份。
                if check_date and parsed < check_date - timedelta(days=180):
                    parsed = parsed.replace(year=year + 1)
                return parsed
            return datetime.strptime(s0, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def normalize_spec(value: str | None) -> str:
    """规格比较仅忽略大小写、首尾空格和连续空白。"""
    if value is None:
        return ""
    return " ".join(str(value).strip().split()).casefold()


def evaluate_spec(
    tiktok_spec: str | None,
    amazon_spec: str | None,
) -> tuple[str, str]:
    local = normalize_spec(tiktok_spec)
    remote = normalize_spec(amazon_spec)
    if not local:
        return "TikTok规格为空", "TikTok 导出表没有可比较的规格"
    if not remote:
        return "规格数据不可用", "Amazon 未提取到明确的当前主规格"
    # TikTok 规格常将颜色、尺寸等组合为逗号分隔文本；需求只比较
    # Amazon 主规格，因此主规格与任一 TikTok 规格分段一致即可。
    local_parts = [normalize_spec(part) for part in str(tiktok_spec).split(",")]
    if remote == local or remote in local_parts:
        return "规格一致", "忽略大小写及空白差异后，Amazon主规格与TikTok对应规格一致"
    return "规格不一致", f"TikTok规格 {tiktok_spec!s}，Amazon主规格 {amazon_spec!s}"


@dataclass
class RuleResult:
    amazon_price: float | None
    suggested_local: float | None
    diff_amount: float | None
    diff_pct: float | None
    price_flag: str
    delivery_raw: str | None
    delivery_date: date | None
    allow_date: date
    delivery_flag: str
    overall: str
    advice: str
    check_date: date


def evaluate_price(
    local_price: float | None,
    amazon_price: float | None,
    *,
    multiplier: float = 2.8,
    threshold: float = 0.10,
    source_bad: bool = False,
) -> tuple[str, float | None, float | None, float | None]:
    if source_bad:
        return "货源异常", None, None, None
    if amazon_price is None:
        return "价格数据不可用", None, None, None
    suggested = round(amazon_price * multiplier, 2)
    if local_price is None or not isinstance(local_price, (int, float)) or local_price <= 0:
        return "本地价格异常", suggested, None, None
    diff_amount = round(suggested - float(local_price), 2)
    diff_pct = abs(suggested - float(local_price)) / float(local_price)
    flag = "价格需检查" if diff_pct >= threshold else "价格正常"
    return flag, suggested, diff_amount, round(diff_pct * 100, 2)


def evaluate_delivery(
    delivery_time: str | None,
    *,
    latest_allowed_date: date,
    check_date: date | None = None,
    captcha: bool = False,
    robot: bool = False,
    http_error: bool = False,
) -> tuple[str, date | None, date]:
    """按用户指定的最晚送达日期判断；截止日期当天也算正常。"""
    check_date = check_date or us_today()
    allow = latest_allowed_date
    if captcha or robot:
        return "配送数据不可用", None, allow
    if http_error and not delivery_time:
        return "配送数据不可用", None, allow
    d = parse_delivery_first_day(
        delivery_time,
        check_date.year,
        check_date=check_date,
    )
    if d is None:
        return ("配送需人工确认" if delivery_time else "配送数据不可用"), None, allow
    if d <= allow:
        return "配送正常", d, allow
    return "配送需检查", d, allow


def overall_status(
    price_flag: str,
    delivery_flag: str,
    *,
    not_found: bool = False,
    captcha: bool = False,
    robot: bool = False,
    source_status: str | None = None,
) -> str:
    if not_found:
        return "链接或 ASIN 异常"
    if captcha or robot:
        return "接口数据不可用"
    if price_flag == "货源异常" or source_status == "货源异常":
        return "货源异常"
    p_bad = price_flag == "价格需检查"
    d_bad = delivery_flag == "配送需检查"
    if p_bad and d_bad:
        return "价格和配送均需检查"
    if p_bad:
        return "价格需检查"
    if d_bad:
        return "配送需检查"
    if price_flag in ("价格数据不可用", "本地价格异常") and delivery_flag in (
        "配送需人工确认",
        "配送数据不可用",
    ):
        return "接口数据不可用"
    if price_flag in ("价格数据不可用", "本地价格异常"):
        return "接口数据不可用"
    if delivery_flag == "配送需人工确认":
        return "配送需人工确认"
    if price_flag == "价格正常" and delivery_flag == "配送正常":
        return "正常"
    if price_flag == "价格正常" and delivery_flag == "配送数据不可用":
        return "配送需人工确认"
    return price_flag or delivery_flag or "接口数据不可用"


def advice_for(status: str) -> str:
    return {
        "正常": "无需处理",
        "价格需检查": "检查并更新 TikTok 售价",
        "配送需检查": "检查 Amazon 配送时间或更换货源",
        "价格和配送均需检查": "同步价格前先确认货源是否还能继续销售",
        "货源异常": "暂停销售或更换货源",
        "链接或 ASIN 异常": "核对平台SKU/ASIN 是否正确",
        "接口数据不可用": "人工打开 Amazon 链接检查；如频繁失败请配置代理后断点续跑",
        "配送需人工确认": "人工确认配送时效",
    }.get(status, "人工复核")


def evaluate_row(
    *,
    local_price: float | None,
    amazon_price: float | None,
    delivery_time: str | None,
    not_found: bool = False,
    captcha: bool = False,
    robot: bool = False,
    unavailable: bool = False,
    source_status: str | None = None,
    http_error: bool = False,
    multiplier: float = 2.8,
    threshold: float = 0.10,
    check_date: date | None = None,
    latest_allowed_date: date | None = None,
) -> RuleResult:
    check_date = check_date or us_today()
    if latest_allowed_date is None:
        raise ValueError("必须指定允许最晚送达日期 latest_allowed_date")
    source_flag = (
        "货源异常"
        if unavailable or source_status in ("明确缺货", "暂无当前购买报价")
        else None
    )

    price_flag, suggested, diff_amt, diff_pct = evaluate_price(
        local_price,
        amazon_price,
        multiplier=multiplier,
        threshold=threshold,
        source_bad=bool(source_flag == "货源异常"),
    )
    if source_flag == "货源异常":
        price_flag = "货源异常"

    delivery_flag, d_date, allow = evaluate_delivery(
        delivery_time,
        latest_allowed_date=latest_allowed_date,
        check_date=check_date,
        captcha=captcha,
        robot=robot,
        http_error=http_error,
    )
    overall = overall_status(
        price_flag,
        delivery_flag,
        not_found=not_found,
        captcha=captcha,
        robot=robot,
        source_status=source_flag,
    )
    return RuleResult(
        amazon_price=amazon_price,
        suggested_local=suggested,
        diff_amount=diff_amt,
        diff_pct=diff_pct,
        price_flag=price_flag,
        delivery_raw=delivery_time,
        delivery_date=d_date,
        allow_date=allow,
        delivery_flag=delivery_flag,
        overall=overall,
        advice=advice_for(overall),
        check_date=check_date,
    )
