#!/usr/bin/env python3
"""
Forecast Guard 决策逻辑回测脚本
复现首尔 2026-02-12 的 FG 锁定决策
"""

from datetime import datetime, timezone, timedelta
import csv

# 配置参数 (与 .env 一致)
FORECAST_GUARD_PEAK_THRESHOLD_C = 1.5
SEOUL_TZ_OFFSET = 9

def local_hour(dt_utc, offset):
    """计算当地时间小时"""
    local = dt_utc + timedelta(hours=offset)
    return local.hour + local.minute / 60.0

def extract_peaks(series, tz_offset):
    """从预报序列中提取 12:00-24:00 的局部极值点 (波峰)"""
    day_points = [(dt, t) for dt, t in series if 12 <= local_hour(dt, tz_offset) < 24]
    if len(day_points) < 2:
        return []

    peaks = []
    for i in range(1, len(day_points) - 1):
        t_prev = day_points[i-1][1]
        t_curr = day_points[i][1]
        t_next = day_points[i+1][1]

        if t_curr > t_prev and t_curr >= t_next:
            peaks.append({
                "time": day_points[i][0],
                "local_hour": local_hour(day_points[i][0], tz_offset),
                "temp": t_curr
            })

    # 边界补齐
    if len(day_points) >= 2 and day_points[-1][1] > day_points[-2][1]:
        peaks.append({
            "time": day_points[-1][0],
            "local_hour": local_hour(day_points[-1][0], tz_offset),
            "temp": day_points[-1][1]
        })
    return peaks

def analyze_forecast(source_name, pts, noaa_curr, day_max_so_far, tz_offset, now_utc):
    """分析单个数据源的决策逻辑"""
    print(f"\n{'='*60}")
    print(f"数据源: {source_name}")
    print(f"NOAA当前实测: {noaa_curr}°C")
    print(f"当日最高实测: {day_max_so_far}°C")
    print(f"分析时间 (UTC): {now_utc}")
    print(f"分析时间 (首尔): {now_utc + timedelta(hours=tz_offset)}")

    # 1. 过滤当天的数据点
    local_date = (now_utc + timedelta(hours=tz_offset)).date()
    day_pts = []
    for dt_utc, temp in pts:
        if (dt_utc + timedelta(hours=tz_offset)).date() == local_date:
            day_pts.append((dt_utc, temp))

    if not day_pts:
        print("  ❌ 无当天数据")
        return None

    # 2. 计算 bias
    now_temp_raw = None
    for dt_utc, temp in day_pts:
        if dt_utc <= now_utc:
            if now_temp_raw is None or abs((dt_utc - now_utc).total_seconds()) < abs((now_temp_raw[0] - now_utc).total_seconds()):
                now_temp_raw = (dt_utc, temp)

    if now_temp_raw is None:
        # 找最近的未来点
        for dt_utc, temp in day_pts:
            if dt_utc > now_utc:
                now_temp_raw = (dt_utc, temp)
                break

    if now_temp_raw is None:
        print("  ❌ 无法找到当前温度")
        return None

    bias = noaa_curr - now_temp_raw[1]
    print(f"\n  📊 偏差分析:")
    print(f"     预报当前温度 (Nearest): {now_temp_raw[1]:.1f}°C @ {now_temp_raw[0] + timedelta(hours=tz_offset)}")
    print(f"     NOAA当前实测: {noaa_curr}°C")
    print(f"     Bias (校正值): {bias:+.1f}°C")

    # 3. Bias 校正
    corrected = [(dt, temp + bias) for dt, temp in day_pts]

    # 4. 提取下午峰值 (12-17点)
    afternoon_pts = [(dt, t) for dt, t in corrected if 12 <= local_hour(dt, tz_offset) < 17]
    afternoon_max = max([t for _, t in afternoon_pts]) if afternoon_pts else -999.0
    print(f"\n  🌞 下午分析 (12-17点):")
    print(f"     预报最高: {afternoon_max:.1f}°C")

    # 5. 日间基准
    day_ref_max = max(afternoon_max, day_max_so_far)
    print(f"     日间基准 (max): {day_ref_max:.1f}°C")

    # 6. 提取夜间峰值 (17点后)
    night_peaks = [p for p in extract_peaks(corrected, tz_offset) if p["local_hour"] >= 17]
    print(f"\n  🌙 夜间分析 (17点后):")
    print(f"     夜间峰值数量: {len(night_peaks)}")
    for p in night_peaks:
        print(f"       - {p['local_hour']:.1f}h: {p['temp']:.1f}°C")

    # 7. 风险判定
    risk_threshold = FORECAST_GUARD_PEAK_THRESHOLD_C
    risky = False
    risk_desc = "OK"
    night_peak_dt = None

    for np in night_peaks:
        # 核心拦截逻辑: night_peak >= day_ref_max - 1.5C
        threshold = day_ref_max - risk_threshold
        if np["temp"] >= threshold:
            risky = True
            night_peak_dt = np["time"]
            risk_desc = f"夜间峰值风险[{np['local_hour']:.1f}h/{np['temp']:.1f}C] (阈值: {threshold:.1f}°C)"
            print(f"\n  ⚠️  风险判定: 命中!")
            print(f"       夜间峰值: {np['temp']:.1f}°C")
            print(f"       日间基准: {day_ref_max:.1f}°C")
            print(f"       阈值: {threshold:.1f}°C (day_ref_max - 1.5)")
            print(f"       原因: {risk_desc}")
            break

    if not risky:
        print(f"\n  ✅ 无风险")
        if night_peaks:
            np = night_peaks[0]
            threshold = day_ref_max - risk_threshold
            print(f"       夜间峰值: {np['temp']:.1f}°C")
            print(f"       日间基准: {day_ref_max:.1f}°C")
            print(f"       阈值: {threshold:.1f}°C")

    return {
        "bias": bias,
        "afternoon_peak": afternoon_max,
        "day_ref_max": day_ref_max,
        "night_peaks": night_peaks,
        "risky": risky,
        "risk_desc": risk_desc
    }

def main():
    # 从服务器下载的最新forecast文件
    csv_file = "/Users/liangxu/Documents/创业项目/虚拟币量化交易/Weather_Polymarket/code/data/recordings/forecast_raw_seoul_20260212_1036.csv"

    print("="*60)
    print("Forecast Guard 决策逻辑回测")
    print("="*60)

    # 读取数据
    data = {}
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            src = row['source']
            if src not in data:
                data[src] = []

            # 解析时间
            ts_utc = datetime.strptime(row['timestamp_utc'], "%Y-%m-%d %H:%M:%S")
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)

            # 提取12-24点数据
            for h in range(12, 25):
                col = f"Local_{h}h"
                if col in row and row[col]:
                    try:
                        temp = float(row[col])
                        # 将本地小时转换为UTC时间
                        local_dt = datetime.strptime(row['timestamp_local'], "%Y-%m-%d %H:%M:%S")
                        local_dt = local_dt.replace(hour=h, minute=0, second=0)
                        utc_dt = local_dt - timedelta(hours=SEOUL_TZ_OFFSET)
                        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
                        data[src].append((utc_dt, temp))
                    except:
                        pass

    # 当前状态 (从实时记录中获取)
    # 首尔 17:13 的状态
    now_utc = datetime(2026, 2, 12, 8, 13, 0, tzinfo=timezone.utc)  # 17:13 首尔 = 08:13 UTC
    noaa_curr = 6.0  # NOAA当前实测
    day_max_so_far = 7.0  # 当日最高实测

    print(f"\n分析时间点: 首尔 {now_utc + timedelta(hours=SEOUL_TZ_OFFSET)}")
    print(f"NOAA当前: {noaa_curr}°C, 当日最高: {day_max_so_far}°C")

    # 分析各数据源
    results = {}
    for src, pts in data.items():
        if pts:
            results[src] = analyze_forecast(src, pts, noaa_curr, day_max_so_far, SEOUL_TZ_OFFSET, now_utc)

    # 汇总
    print(f"\n{'='*60}")
    print("📋 汇总分析")
    print("="*60)

    risk_count = sum(1 for r in results.values() if r and r["risky"])
    available = len([r for r in results.values() if r is not None])

    print(f"\n可用数据源: {available}")
    print(f"风险源数量: {risk_count}")
    print(f"风险源阈值: 1")

    if risk_count >= 1:
        print(f"\n🔒 决策: LOCKED (风险源 {risk_count} >= 1)")
        for src, r in results.items():
            if r and r["risky"]:
                print(f"   - {src}: {r['risk_desc']}")
    else:
        print(f"\n🔓 决策: PASS")

if __name__ == "__main__":
    main()
