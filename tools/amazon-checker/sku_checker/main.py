"""主流程：读表 → 采集 → 规则 → 报告。默认直连，可选受控并发和代理。"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sku_checker import __version__
from sku_checker.amazon_client import AmazonClient
from sku_checker.config import load_config
from sku_checker.excel_io import load_sku_rows
from sku_checker.proxy_pool import ProxyPool
from sku_checker.report import needs_retry, write_report
from sku_checker.rules import advice_for, evaluate_row, evaluate_spec, us_today


def cooldown_bounds(level: int) -> tuple[int, int]:
    """返回第 1-3 次自动熔断恢复的冷却秒数范围。"""
    ranges = ((180, 300), (480, 720), (900, 1500))
    if not 1 <= int(level) <= len(ranges):
        raise ValueError("自动熔断恢复次数只允许 1 到 3")
    return ranges[int(level) - 1]


def _wait_for_cooldown(
    seconds: int,
    *,
    level: int,
    completed: int,
    total: int,
    notify: Callable[..., None],
    cancel_event: Any | None,
    wait_step: Callable[[float], None] = time.sleep,
) -> bool:
    """倒计时冷却；返回 False 表示用户已取消。"""
    remaining = max(0, int(seconds))
    while remaining > 0:
        if cancel_event is not None and cancel_event.is_set():
            return False
        notify(
            "cooldown", current=completed, total=total, asin="",
            status=f"第{level}次熔断冷却，剩余 {remaining} 秒",
            cooldown_level=level, remaining_seconds=remaining,
        )
        wait_step(min(1, remaining))
        remaining -= 1
    return not (cancel_event is not None and cancel_event.is_set())


def _state_load(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"done": {}}
    return {"done": {}}


def _state_save(path: Path, state: dict[str, Any]) -> None:
    """原子写入断点，避免进程意外停止时留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _row_key(item: dict[str, Any]) -> str:
    return f"{item.get('row_num')}:{item.get('asin')}"


def _update_progress(
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    done: dict[str, Any],
    *,
    input_excel: str,
    current_index: int | None = None,
    current_item: dict[str, Any] | None = None,
) -> None:
    """保存已完成位置以及下一条待同步数据。"""
    next_index = None
    next_item = None
    for index, item in enumerate(rows, 1):
        if _row_key(item) not in done:
            next_index, next_item = index, item
            break
    state.update({
        "version": 2,
        "input_excel": str(Path(input_excel).resolve()),
        "total": len(rows),
        "completed": len(done),
        "last_completed_index": current_index,
        "last_completed_row_num": current_item.get("row_num") if current_item else None,
        "last_completed_asin": current_item.get("asin") if current_item else None,
        "next_index": next_index,
        "next_row_num": next_item.get("row_num") if next_item else None,
        "next_asin": next_item.get("asin") if next_item else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed" if next_item is None else "in_progress",
        "done": done,
    })


def _try_state_save(path: Path, state: dict[str, Any], *, progress: str, asin: str) -> None:
    try:
        _state_save(path, state)
    except Exception as exc:
        print(f"警告: [{progress}] {asin or '-'} 断点状态保存失败: {type(exc).__name__}: {exc}")


def _make_client(cfg: dict[str, Any], pool: ProxyPool, out_dir: Path) -> AmazonClient:
    delay = cfg.get("delay_between_requests") or [1.5, 3.5]
    if isinstance(delay, (int, float)):
        delay = [float(delay), float(delay)]
    return AmazonClient(
        zip_code=str(cfg.get("zip_code") or "91730"),
        timeout=int(cfg.get("request_timeout") or 25),
        max_retries=int(cfg.get("max_retries") or 2),
        delay_range=(float(delay[0]), float(delay[1])),
        impersonate=str(cfg.get("impersonate") or "chrome"),
        proxy_pool=pool,
        captcha_backoff_base=float(cfg.get("captcha_backoff_base") or 8),
        captcha_backoff_max=float(cfg.get("captcha_backoff_max") or 60),
        debug_dir=str(out_dir / "debug_pages") if cfg.get("save_debug_pages") else None,
    )


def _stock_result(fr: Any, warning_threshold: int) -> tuple[int | str, str]:
    """将当前商品库存信号转换为报告值和建议。"""
    if fr.stock_quantity is not None:
        quantity = int(fr.stock_quantity)
        return quantity, "库存不足" if quantity < warning_threshold else "正常"
    explicitly_unavailable = fr.in_stock is False or fr.source_status in (
        "明确缺货", "暂无当前购买报价"
    )
    no_offer_signals = (
        fr.price is None
        and fr.has_add_to_cart is not True
        and fr.in_stock is not True
        and fr.purchase_area_detected
    )
    if explicitly_unavailable or no_offer_signals:
        return "无库存", "无库存"
    if fr.in_stock is True or fr.price is not None or fr.has_add_to_cart is True:
        return "库存正常", "正常"
    return "无库存", "无库存"


def _process_item(
    item: dict[str, Any],
    *,
    cfg: dict[str, Any],
    checked_at: str,
    check_date: Any,
    multiplier: float,
    threshold: float,
    latest_allowed_date: date,
    client: AmazonClient | None,
) -> dict[str, Any]:
    """完成单个输入行的采集和规则处理；不触碰共享状态。"""
    asin = item.get("asin") or ""
    base = {
        "sku_id": item.get("sku_id"), "product_id": item.get("product_id"),
        "shop_id": item.get("shop_id"),  # <--- 就只加这一行！
        "product_name": item.get("product_name"), "spec": item.get("spec"),
        "asin": asin, "amazon_link": item.get("amazon_link"),
        "local_price": item.get("local_price"), "checked_at": checked_at,
        "source": "Amazon公开页",
    }
    if not item.get("asin_valid"):
        rr = evaluate_row(
            local_price=item.get("local_price"), amazon_price=None, delivery_time=None,
            not_found=True, multiplier=multiplier, threshold=threshold,
            check_date=check_date, latest_allowed_date=latest_allowed_date,
        )
        return {
            **base, "amazon_price": None, "suggested_local": rr.suggested_local,
            "diff_amount": rr.diff_amount, "diff_pct": rr.diff_pct,
            "price_flag": rr.price_flag, "source_status": "链接或ASIN异常",
            "status_reason": "输入 ASIN 格式无效或为空", "delivery_time": None,
            "delivery_option_type": None, "delivery_text": None,
            "target_zip": str(cfg.get("zip_code") or ""), "zip_confirmed": "否",
            "allow_date": rr.allow_date.isoformat(), "delivery_flag": rr.delivery_flag,
            "overall": rr.overall, "advice": rr.advice,
            "error": "ASIN 格式无效或为空", "proxy_used": None,
            "attempt_count": 0, "sync_status": "输入数据无效已跳过",
        }
    if client is None:
        raise RuntimeError("有效 ASIN 缺少 AmazonClient")
    try:
        fr = client.fetch_asin(asin)
        unavailable = fr.in_stock is False and not fr.error and fr.zip_ok is True
        zip_confirmed = fr.zip_ok is True
        rr = evaluate_row(
            local_price=item.get("local_price"), amazon_price=fr.price,
            delivery_time=fr.delivery_time if zip_confirmed else None,
            not_found=fr.not_found, captcha=fr.captcha, robot=fr.robot_block,
            unavailable=bool(unavailable), source_status=fr.source_status,
            http_error=bool(fr.error and not fr.ok), multiplier=multiplier,
            threshold=threshold, check_date=check_date,
            latest_allowed_date=latest_allowed_date,
        )
        err = fr.error
        spec_flag, spec_reason = evaluate_spec(item.get("spec"), fr.amazon_spec)
        if fr.spec_parse_reason and spec_flag == "规格数据不可用":
            spec_reason = fr.spec_parse_reason
        if fr.captcha or fr.robot_block:
            err = (err + "; " if err else "") + "访问受限/验证码"
        if not zip_confirmed:
            zip_err = f"目标邮编未确认：配置 {cfg.get('zip_code')}，页面显示 {fr.location_line or '空'}"
            err = (err + "; " if err else "") + zip_err
        overall, advice = rr.overall, rr.advice
        stock_value, stock_advice = _stock_result(
            fr, int(cfg.get("stock_warning_threshold", 5))
        )
        if fr.error or not zip_confirmed:
            stock_value, stock_advice = "数据不可用", "重新采集"
            if overall == "货源异常":
                overall = "接口数据不可用"
                advice = advice_for(overall)
        if spec_flag == "规格不一致":
            overall = "规格需检查" if rr.overall == "正常" else f"{rr.overall}；规格需检查"
            advice = f"{rr.advice}；核对 TikTok 与 Amazon 规格是否对应"
        return {
            **base, "amazon_price": fr.price, "suggested_local": rr.suggested_local,
            "diff_amount": rr.diff_amount, "diff_pct": rr.diff_pct,
            "price_flag": rr.price_flag, "source_status": fr.source_status,
            "status_reason": fr.status_reason, "amazon_spec": fr.amazon_spec,
            "amazon_spec_dimension": fr.amazon_spec_dimension, "spec_flag": spec_flag,
            "spec_reason": spec_reason, "stock_status": stock_value,
            "stock_advice": stock_advice, "availability_text": fr.availability_text,
            "delivery_time": fr.delivery_time,
            "delivery_option_type": fr.delivery_option_type,
            "delivery_extract_source": fr.delivery_extract_source,
            "delivery_text": fr.delivery_text,
            "delivery_diagnostic": fr.delivery_diagnostic,
            "target_zip": str(cfg.get("zip_code") or ""),
            "zip_confirmed": "是" if zip_confirmed else "否",
            "allow_date": rr.allow_date.isoformat(), "delivery_flag": rr.delivery_flag,
            "overall": overall, "advice": advice, "location_line": fr.location_line,
            "has_add_to_cart": "是" if fr.has_add_to_cart else ("否" if fr.has_add_to_cart is False else ""),
            "proxy_used": fr.proxy_used or "直连", "attempt_count": fr.attempt_count,
            "sync_status": "同步失败已跳过" if fr.error and not fr.ok else "同步完成",
            "error": err, "title": fr.title,
        }
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        return {
            **base, "amazon_price": None, "suggested_local": None,
            "diff_amount": None, "diff_pct": None, "price_flag": "价格数据不可用",
            "source_status": "访问或采集失败", "status_reason": err,
            "delivery_time": None, "delivery_option_type": None,
            "delivery_extract_source": None, "delivery_text": None,
            "delivery_diagnostic": "单条处理异常，未执行配送结构诊断",
            "target_zip": str(cfg.get("zip_code") or ""),
            "zip_confirmed": "否",
            "allow_date": latest_allowed_date.isoformat(),
            "delivery_flag": "配送数据不可用", "overall": "接口数据不可用",
            "advice": advice_for("接口数据不可用"), "location_line": None,
            "has_add_to_cart": "", "proxy_used": None,
            "attempt_count": int(cfg.get("max_retries", 2)) + 1,
            "sync_status": "同步失败已跳过", "error": err, "title": None,
        }


def run(
    input_excel: str, *, config_path: str | None = None, limit: int | None = None,
    output: str | None = None, resume_override: bool | None = None,
    retry_failed_only: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: Any | None = None,
) -> Path:
    def notify(event: str, **payload: Any) -> None:
        if progress_callback is not None:
            try:
                progress_callback({"event": event, **payload})
            except Exception as exc:
                print(f"警告: 进度回调失败: {type(exc).__name__}: {exc}")

    cfg = load_config(config_path)
    workers = int(cfg.get("workers", 1))
    out_dir = Path(cfg.get("output_dir") or "./output")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_in = load_sku_rows(input_excel)
    if limit is not None:
        rows_in = rows_in[:max(0, int(limit))]

    proxy_cfg = cfg.get("proxy") or {}
    pool = ProxyPool(proxy_cfg)
    state_path = Path(cfg.get("state_file") or out_dir / "run_state.json")
    delivery_cfg = cfg.get("delivery") or {}
    latest_allowed_raw = str(delivery_cfg.get("latest_allowed_date") or "").strip()
    if not latest_allowed_raw:
        raise ValueError(
            "请在配置中指定 delivery.latest_allowed_date（格式 YYYY-MM-DD）"
        )
    latest_allowed_date = date.fromisoformat(latest_allowed_raw)
    resume = bool(cfg.get("resume", True)) if resume_override is None else bool(resume_override)
    state = _state_load(state_path) if (resume or retry_failed_only) else {"done": {}}
    config_signature = {
        "input_excel": str(Path(input_excel).resolve()),
        "latest_allowed_date": latest_allowed_date.isoformat(),
        "zip_code": str(cfg.get("zip_code") or ""),
        "stock_warning_threshold": int(cfg.get("stock_warning_threshold", 5)),
        # 解析规则升级后，旧断点中的空规格/空配送不能继续混入新报告。
        "parser_version": __version__,
    }
    saved_signature = state.get("config_signature")
    if resume and saved_signature not in (None, config_signature):
        changed_keys = sorted(
            key for key in set(saved_signature or {}) | set(config_signature)
            if (saved_signature or {}).get(key) != config_signature.get(key)
        )
        reason = "、".join(changed_keys) if changed_keys else "运行配置"
        print(f"检测到 {reason} 已变化，将忽略旧断点并重新检查。")
        state = {"done": {}}
    state["config_signature"] = config_signature
    done: dict[str, Any] = state.get("done") or {}
    if retry_failed_only:
        retry_keys = [key for key, row in done.items() if needs_retry(row)]
        if not retry_keys:
            raise ValueError("断点中没有需要重试的失败项")
        for key in retry_keys:
            done.pop(key, None)
        print(f"重试失败项：本次只重新检查 {len(retry_keys)} 条，其他 {len(done)} 条结果保留。")
    if resume and done:
        next_entry = next(((i, x) for i, x in enumerate(rows_in, 1) if _row_key(x) not in done), None)
        if next_entry:
            print(f"检测到断点：已完成 {len(done)}/{len(rows_in)}，上次最后完成 {state.get('last_completed_asin') or '未知'}，本次从第 {next_entry[0]} 条 {next_entry[1].get('asin') or '-'} 继续。")
        else:
            print(f"检测到断点：{len(done)}/{len(rows_in)} 条均已有结果，将重建报告。")

    max_blocks = int(cfg.get("max_consecutive_blocks") or 8)
    checked_at = datetime.now(timezone.utc).isoformat()
    check_date = us_today()
    mult = float(cfg.get("price_multiplier", 2.8))
    thr = float(cfg.get("price_threshold", 0.10))
    total = len(rows_in)
    cancelled = False
    stopped = False
    recovery_level = 0
    clients: list[AmazonClient] = []

    notify("run_started", current=0, total=total, asin="", status="开始检查")
    for i, item in enumerate(rows_in, 1):
        if _row_key(item) in done:
            print(f"[{i}/{total}] skip {item.get('asin')} (resume)")
            notify("row_completed", current=i, total=total, asin=item.get("asin") or "", status="已跳过（断点）")

    while True:
        pending_items = [(i, item) for i, item in enumerate(rows_in, 1) if _row_key(item) not in done]
        if not pending_items or cancelled or stopped:
            break

        generation_clients: list[AmazonClient] = []
        clients_lock = threading.Lock()
        local = threading.local()
        trip_requested = False

        def thread_client() -> AmazonClient:
            client = getattr(local, "client", None)
            if client is None:
                client = _make_client(cfg, pool, out_dir)
                local.client = client
                with clients_lock:
                    generation_clients.append(client)
                    clients.append(client)
            return client

        def work(index: int, item: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
            client = thread_client() if item.get("asin_valid") else None
            row = _process_item(
                item, cfg=cfg, checked_at=checked_at, check_date=check_date,
                multiplier=mult, threshold=thr, latest_allowed_date=latest_allowed_date,
                client=client,
            )
            return index, item, row

        def merge(index: int, item: dict[str, Any], row: dict[str, Any]) -> None:
            nonlocal stopped, trip_requested
            asin = item.get("asin") or ""
            done[_row_key(item)] = row
            _update_progress(state, rows_in, done, input_excel=input_excel, current_index=index, current_item=item)
            _try_state_save(state_path, state, progress=f"{index}/{total}", asin=asin)
            if not item.get("asin_valid"):
                print(f"[{index}/{total}] {asin or '-'} ASIN无效")
                status = "ASIN无效"
            elif row.get("overall") == "接口数据不可用":
                detail = (
                    row.get("error")
                    or row.get("delivery_diagnostic")
                    or row.get("status_reason")
                    or "本次页面缺少可识别的价格或配送数据"
                )
                print(f"[{index}/{total}] {asin} 数据未取全，已记录并继续下一条: {detail}")
                status = "数据未取全"
            else:
                print(f"[{index}/{total}] {asin} price={row.get('amazon_price')} del={row.get('delivery_time')} flag={row.get('overall')} spec={row.get('spec_flag')} proxy={row.get('proxy_used')}" + (f" err={row.get('error')}" if row.get("error") else ""))
                status = row.get("overall") or "同步完成"
            notify("row_completed", current=index, total=total, asin=asin, status=status)
            recent = [done[_row_key(x)] for x in rows_in if _row_key(x) in done][-5:]
            if sum(1 for r in recent if "407" in str(r.get("error") or "")) >= 3:
                print("\n连续代理 407 认证失败。请检查代理用户名/密码后使用断点续跑。")
                stopped = True
            aggregate_blocks = sum(
                getattr(c, "consecutive_blocks", 0)
                if isinstance(getattr(c, "consecutive_blocks", 0), (int, float)) else 0
                for c in generation_clients
            )
            if aggregate_blocks >= max_blocks:
                trip_requested = True

        if workers == 1:
            serial_client = _make_client(cfg, pool, out_dir) if any(x.get("asin_valid") for _, x in pending_items) else None
            if serial_client is not None:
                generation_clients.append(serial_client)
                clients.append(serial_client)
            for i, item in pending_items:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                asin = item.get("asin") or ""
                notify("row_started", current=i, total=total, asin=asin, status="检查中")
                row = _process_item(item, cfg=cfg, checked_at=checked_at, check_date=check_date, multiplier=mult, threshold=thr, latest_allowed_date=latest_allowed_date, client=serial_client if item.get("asin_valid") else None)
                merge(i, item, row)
                if trip_requested or stopped:
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sku-check") as executor:
                inflight: dict[Future, tuple[int, dict[str, Any]]] = {}
                next_pos = 0
                while (next_pos < len(pending_items) or inflight) and not trip_requested and not stopped:
                    while next_pos < len(pending_items) and len(inflight) < workers:
                        if cancel_event is not None and cancel_event.is_set():
                            cancelled = True
                            break
                        i, item = pending_items[next_pos]
                        next_pos += 1
                        notify("row_started", current=i, total=total, asin=item.get("asin") or "", status="检查中")
                        inflight[executor.submit(work, i, item)] = (i, item)
                    if not inflight:
                        break
                    finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for future in finished:
                        inflight.pop(future)
                        i, item, row = future.result()
                        merge(i, item, row)
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                # 停止提交新任务；已发出的请求安全收尾并写入断点。
                while inflight:
                    finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for future in finished:
                        inflight.pop(future)
                        i, item, row = future.result()
                        merge(i, item, row)

        for client in generation_clients:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        if cancelled or stopped:
            break
        if not trip_requested:
            continue

        recovery_level += 1
        _update_progress(state, rows_in, done, input_excel=input_excel)
        state["status"] = "cooldown"
        state["cooldown_level"] = recovery_level
        _try_state_save(state_path, state, progress=f"{len(done)}/{total}", asin="-")
        if recovery_level > 3:
            print("\n本次运行已完成 3 次自动冷却，仍再次触发熔断。已保存断点并停止，请稍后继续。")
            stopped = True
            break

        low, high = cooldown_bounds(recovery_level)
        cooldown_seconds = random.randint(low, high)
        print(
            f"\n连续访问受限达到熔断阈值。已保存 {len(done)}/{total} 条结果；"
            f"第 {recovery_level} 次自动冷却 {cooldown_seconds} 秒，结束后重建 Session 并继续。"
        )
        if not _wait_for_cooldown(
            cooldown_seconds, level=recovery_level, completed=len(done), total=total,
            notify=notify, cancel_event=cancel_event,
        ):
            cancelled = True
            break
        print(f"第 {recovery_level} 次冷却结束，正在重建网络 Session 并从断点继续。")
        notify("cooldown_finished", current=len(done), total=total, asin="", status="冷却结束，继续检查")

    if cancelled:
        print(f"已收到停止请求，将用现有 {len(done)}/{total} 条结果生成部分报告。")
        notify("cancelled", current=len(done), total=total, asin="", status="正在生成部分报告")
        _update_progress(state, rows_in, done, input_excel=input_excel)
        state["status"] = "cancelled"
        _try_state_save(state_path, state, progress=f"{len(done)}/{total}", asin="-")

    # 无论任务完成顺序如何，都严格按原始 Excel 行序重建报告；重复 ASIN 行不会合并。
    results = [done[_row_key(item)] for item in rows_in if _row_key(item) in done]
    price_ok = sum(1 for r in results if r.get("amazon_price") is not None)
    del_ok = sum(1 for r in results if r.get("delivery_time") and r.get("zip_confirmed") == "是")
    zip_ok_count = sum(1 for r in results if r.get("zip_confirmed") == "是")
    blocked = sum(1 for r in results if "访问受限" in str(r.get("error") or ""))
    errors = sum(1 for r in results if r.get("error"))
    odist: dict[str, int] = {}
    for row in results:
        key = row.get("overall") or "?"
        odist[key] = odist.get(key, 0) + 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = Path(output) if output else out_dir / f"SKU检查报告_{ts}.xlsx"
    proxy_mode = str(proxy_cfg.get("mode") or "none") if proxy_cfg.get("enabled") else "none"
    write_report(results, out_file, meta={
        "checked_at": checked_at, "input_excel": str(input_excel),
        "zip_code": cfg.get("zip_code"), "price_multiplier": mult,
        "price_threshold": thr, "stock_warning_threshold": int(cfg.get("stock_warning_threshold", 5)),
        "latest_allowed_date": latest_allowed_date.isoformat(),
        "proxy_mode": proxy_mode,
        "stats": {"price_ok": f"{price_ok}/{len(results)}", "delivery_ok": f"{del_ok}/{len(results)}", "zip_ok": f"{zip_ok_count}/{len(results)}", "blocked": blocked, "errors": errors, "overall_dist": odist},
    })
    def numeric_stat(client: AmazonClient, name: str) -> int:
        value = getattr(client, name, 0)
        return int(value) if isinstance(value, (int, float)) else 0

    total_captcha = sum(numeric_stat(c, "total_captcha") for c in clients)
    total_soft_block = sum(numeric_stat(c, "total_soft_block") for c in clients)
    total_consecutive = sum(numeric_stat(c, "consecutive_blocks") for c in clients)
    print(f"\n报告已生成: {out_file}")
    print(f"价格成功 {price_ok}/{len(results)} | 配送成功 {del_ok}/{len(results)}")
    print(f"总状态: {odist}")
    print(f"代理池状态: {pool.status()}")
    print(f"验证码/拦截统计: captcha={total_captcha} soft_block={total_soft_block} consecutive={total_consecutive}")
    notify("run_completed", current=len(done), total=total, asin="", status="已停止，部分报告已生成" if cancelled else "检查完成", report_path=str(out_file), cancelled=cancelled)
    return out_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amazon SKU 检查工具（默认直连，可选代理）")
    parser.add_argument("excel", nargs="?", help="TikTok 导出的 SKU Excel 路径")
    parser.add_argument("-c", "--config", default=None, help="config.yaml 路径")
    parser.add_argument("-o", "--output", default=None, help="输出报告路径")
    parser.add_argument("-n", "--limit", type=int, default=None, help="只跑前 N 条（调试）")
    parser.add_argument("--resume", action="store_true", help="强制开启断点续跑")
    parser.add_argument("--no-resume", action="store_true", help="忽略断点重新跑")
    parser.add_argument("--retry-failed", action="store_true", help="只重新检查断点中的取数失败项")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    excel = args.excel or cfg.get("input_excel")
    if not excel:
        print("请指定 Excel 路径，例如:\n  python -m sku_checker.main data.xlsx")
        return 2
    if not Path(excel).exists():
        print(f"文件不存在: {excel}")
        return 2
    if sum(bool(x) for x in (args.resume, args.no_resume, args.retry_failed)) > 1:
        parser.error("--resume、--no-resume 和 --retry-failed 不能同时使用")
    resume_override = True if args.resume else (False if args.no_resume else None)
    try:
        run(
            excel, config_path=args.config, limit=args.limit, output=args.output,
            resume_override=resume_override, retry_failed_only=args.retry_failed,
        )
        return 0
    except KeyboardInterrupt:
        print("\n用户中断，进度已保存，可 --resume 继续")
        return 130
    except Exception as exc:
        print(f"运行失败: {exc}")
        raise


if __name__ == "__main__":
    sys.exit(main())
