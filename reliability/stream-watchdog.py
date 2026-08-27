#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stream-watchdog.py v2 - Hermes 全局断流看门狗(主档案+全部子档案)

监视每个档案 logs/agent.log 的 mid-stream drop 警告
(Stream ended with no finish_reason),
自动对受影响会话执行 `hermes [-p 档案] chat --resume` 注入续跑指令,
直到会话恢复产出。档案列表每轮自动发现,新建子档案无需改配置。

用法:
  pythonw stream-watchdog.py            # 常驻轮询(默认15s,配合开机自启VBS)
  python stream-watchdog.py --status    # 查看运行状态
  python stream-watchdog.py --check     # 单次扫描退出(cron备用模式)
  python stream-watchdog.py --dry-run   # 只检测打印,不真正注入
"""
import argparse, ctypes, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time

HERMES_DIR  = os.path.expandvars(r"%LOCALAPPDATA%\hermes")
PROFILES_DIR = os.path.join(HERMES_DIR, "profiles")
STATE_FILE  = os.path.join(HERMES_DIR, "logs", "stream-watchdog-state.json")
OUT_LOG     = os.path.join(HERMES_DIR, "logs", "stream-watchdog.log")
ALERT_FILE  = os.path.join(HERMES_DIR, "logs", "stream-watchdog-ALERT.txt")
PID_FILE    = os.path.join(HERMES_DIR, "logs", "stream-watchdog.pid")
COMMANDS_FILE = os.path.join(HERMES_DIR, "logs", "stream-watchdog-commands.txt")

DROP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d*\s+WARNING\s+\S+:\s+Stream ended with no finish_reason")
POLL_SEC, NUDGE_TIMEOUT, MAX_ATTEMPTS, COOLDOWN, EVENT_TTL = 15, 1200, 3, 120, 86400
NUDGE_MSG = ("[watchdog] 你上一条回复因上游流式中断被截断。"
             "请从中断处直接继续完成任务,不要重复已输出内容,不要向用户解释本提示。")

def wlog(msg):
    line = "%s | %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(OUT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
def save_state(st):
    st["alive"] = time.time()
    cutoff = time.time() - EVENT_TTL
    st["events"] = {k: v for k, v in st.get("events", {}).items()
                    if isinstance(v, dict) and v.get("ts", 0) > cutoff}
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        wlog("state save failed: %s" % e)

def ts_to_epoch(ts):
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return 0

def db_query(db_path, sql, params=()):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()

def discover_targets():
    """[(profile, log_path, db_path)]。default=主档案;子档案自动发现。"""
    t = [("default",
          os.path.join(HERMES_DIR, "logs", "agent.log"),
          os.path.join(HERMES_DIR, "state.db"))]
    try:
        for name in sorted(os.listdir(PROFILES_DIR)):
            pdir = os.path.join(PROFILES_DIR, name)
            if os.path.isdir(pdir):
                t.append((name,
                          os.path.join(pdir, "logs", "agent.log"),
                          os.path.join(pdir, "state.db")))
    except OSError:
        pass
    return t

def find_target_session(db_path, drop_epoch):
    """按时间就近找活跃会话: 断流行前后10分钟内最接近的未归档会话。"""
    rows = db_query(db_path,
        "SELECT id FROM sessions WHERE archived=0 "
        "ORDER BY ABS(last_activity_at - ?) ASC LIMIT 1", (drop_epoch,))
    if rows:
        lat = db_query(db_path, "SELECT last_activity_at FROM sessions WHERE id=?", (rows[0][0],))
        if lat and lat[0][0] and abs(lat[0][0] - drop_epoch) < 600:
            return rows[0][0]
    rows = db_query(db_path, "SELECT id FROM sessions WHERE archived=0 "
                    "ORDER BY last_activity_at DESC LIMIT 1")
    return rows[0][0] if rows else None

def turn_still_running(db_path, sid):
    """session_turn_leases 有活跃租约 => 回合仍在别处运行,不打扰。"""
    try:
        cols = [r[1] for r in db_query(db_path, "PRAGMA table_info(session_turn_leases)")]
        if not cols:
            return False
        rows = db_query(db_path, "SELECT * FROM session_turn_leases WHERE session_id=?", (sid,))
        if not rows:
            return False
        if "expires_at" in cols:
            exp = cols.index("expires_at")
            return any(r[exp] and float(r[exp]) > time.time() for r in rows)
        return True
    except sqlite3.Error:
        return False

def nudge_session(profile, db_path, sid, dry_run=False):
    hermes = shutil.which("hermes")
    if not hermes:
        wlog("ERROR: hermes 不在 PATH,无法注入")
        return False
    cmd = [hermes]
    if profile != "default":
        cmd += ["-p", profile]
    cmd += ["chat", "--resume", sid, "-q", NUDGE_MSG, "-Q", "--max-turns", "10"]
    tag = profile if profile != "default" else "主档案"
    if dry_run:
        wlog("[dry-run] %s/%s 将注入续跑指令" % (tag, sid))
        return True
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    wlog("注入续跑指令 -> [%s] %s" % (tag, sid))
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=os.path.expanduser("~"), timeout=NUDGE_TIMEOUT,
                           creationflags=flags, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        tail = p.stdout.decode("utf-8", "replace").strip().splitlines()
        wlog("[%s] resume 退出码=%s 输出尾: %s" %
             (tag, p.returncode, tail[-1][:150] if tail else "(空)"))
    except subprocess.TimeoutExpired:
        wlog("WARN: [%s] resume 超时(%ss)" % (tag, NUDGE_TIMEOUT))
        return False
    except OSError as e:
        wlog("ERROR: resume 启动失败: %s" % e)
        return False
    return verify_progress(db_path, sid, since=t0)

def verify_progress(db_path, sid, since=None):
    since = since or time.time() - 30
    rows = db_query(db_path,
        "SELECT COUNT(*) FROM messages WHERE session_id=? AND timestamp>? "
        "AND role IN ('assistant','tool')", (sid, since))
    ok = bool(rows and rows[0][0] > 0)
    wlog("%s [%s] (%d 条新消息)" % ("RECOVERED" if ok else "NOT-RECOVERED", sid, rows[0][0] if rows else 0))
    return ok

def raise_alert(profile, sid, reason):
    wlog("ALERT: [%s] %s 重试%d次仍失败: %s" % (profile, sid, MAX_ATTEMPTS, reason))
    try:
        with open(ALERT_FILE, "a", encoding="utf-8") as f:
            f.write("%s | %s | %s | %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), profile, sid, reason))
    except OSError:
        pass

def handle_drops(profile, db_path, drops, st, dry_run):
    events = st.setdefault("events", {})
    wm = st["targets"].setdefault(_key(profile), {})
    for epoch, raw in drops:
        if epoch <= wm.get("watermark", 0):
            continue
        sig = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        ev = events.get(sig, {"ts": epoch, "attempts": 0, "last_nudge": 0})
        events[sig] = ev
        wm["watermark"] = max(wm.get("watermark", 0), epoch)
        if ev["attempts"] >= MAX_ATTEMPTS:
            continue
        if time.time() - ev["last_nudge"] < COOLDOWN:
            continue
        if not os.path.exists(db_path):
            continue
        sid = find_target_session(db_path, epoch)
        if not sid:
            wlog("[%s] 找不到目标会话,跳过" % profile)
            continue
        if turn_still_running(db_path, sid):
            wlog("[%s] %s 租约活跃中,不打扰" % (profile, sid))
            continue
        ev["attempts"] += 1
        ev["last_nudge"] = time.time()
        if nudge_session(profile, db_path, sid, dry_run):
            ev["attempts"] = MAX_ATTEMPTS
        elif ev["attempts"] >= MAX_ATTEMPTS:
            raise_alert(profile, sid, "多次注入未恢复")

def _key(profile):
    return "profile:" + profile

def scan_file(log_path, from_offset):
    drops, off = [], from_offset
    try:
        size = os.path.getsize(log_path)
        if size < from_offset:
            off = 0
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if off:
                f.seek(off)
            new = f.read()
            off = f.tell()
        for line in new.splitlines():
            m = DROP_RE.match(line.strip())
            if m:
                drops.append((ts_to_epoch(m.group(1)), line.strip()))
    except OSError:
        pass
    return off, drops

def scan_tail(log_path, since_epoch):
    drops = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 300000))
            for line in f.read().splitlines():
                m = DROP_RE.match(line.strip())
                if m and ts_to_epoch(m.group(1)) > since_epoch:
                    drops.append((ts_to_epoch(m.group(1)), line.strip()))
    except OSError:
        pass
    return drops

def process_commands():
    """执行外部投递的命令(格式: exec <python路径> <脚本路径>)。
    用于需要在 Hermes 关闭后仍要继续的维护任务(如桌面端重打包)。"""
    try:
        with open(COMMANDS_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        os.remove(COMMANDS_FILE)
    except OSError:
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    out = open(os.path.join(HERMES_DIR, "logs", "watchdog-exec.log"), "ab")
    for line in lines:
        wlog("CMD 收到: %s" % line)
        parts = line.split(None, 1)
        if parts and parts[0] == "exec" and len(parts) == 2:
            exe, _, script = parts[1].partition(" ")
            try:
                subprocess.Popen([exe.strip(), script.strip()], cwd=HERMES_DIR,
                                 creationflags=flags, stdout=out, stderr=subprocess.STDOUT)
                wlog("CMD 已派生独立进程: %s %s" % (exe.strip(), script.strip()))
            except OSError as e:
                wlog("CMD 启动失败: %s" % e)

def another_instance_alive():
    if not os.path.exists(PID_FILE):
        return None
    try:
        old = int(open(PID_FILE).read().strip())
        h = ctypes.windll.kernel32.OpenProcess(0x100000 | 0x0400, False, old)  # SYNCHRONIZE|QUERY_LIMITED
        if h:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            if code.value == 259:  # STILL_ACTIVE 才算活;僵尸对象(已退出)不算
                return old
    except (OSError, ValueError):
        pass
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="单次扫描退出(cron用)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--interval", type=int, default=POLL_SEC)
    args = ap.parse_args()

    if args.status:
        st = load_state()
        info = {k: v for k, v in st.items() if k != "events"}
        print(json.dumps(info, indent=2, ensure_ascii=False))
        print("active_events:", len(st.get("events", {})))
        dup = another_instance_alive()
        print("running_instance_pid:", dup)
        return

    dup = another_instance_alive()
    if dup and args.check is False:
        wlog("已有实例运行(pid=%s),本实例退出" % dup)
        sys.exit(0)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    st = load_state()
    if st.get("v") != 2:  # 升级到 v2 结构;旧水位作废,重建基线不回放历史
        st = {"v": 2, "targets": {}, "events": {}}

    if args.check:
        for profile, logp, dbp in discover_targets():
            wm = st["targets"].get(_key(profile), {}).get("watermark", time.time() - 3600)
            drops = scan_tail(logp, wm) if os.path.exists(logp) else []
            if drops:
                st["targets"].setdefault(_key(profile), {})
                handle_drops(profile, dbp, drops, st, args.dry_run)
        save_state(st)
        return

    # 常驻模式: 目标逐个确保有 offset+watermark(历史断流不回放)
    for profile, logp, dbp in discover_targets():
        key = _key(profile)
        tgt = st["targets"].setdefault(key, {})
        if "offset" not in tgt or "watermark" not in tgt:
            tgt["offset"] = os.path.getsize(logp) if os.path.exists(logp) else 0
            tgt["watermark"] = time.time()
    save_state(st)
    wlog("看门狗v2启动 pid=%s 监视 %d 个档案" % (os.getpid(), len(st["targets"])))
    while True:
        process_commands()
        for profile, logp, dbp in discover_targets():
            key = _key(profile)
            tgt = st["targets"].get(key)
            if not isinstance(tgt, dict) or "offset" not in tgt:  # 运行中出现的新档案
                tgt = {"offset": os.path.getsize(logp) if os.path.exists(logp) else 0,
                       "watermark": time.time()}
                st["targets"][key] = tgt
                wlog("发现新档案 [%s],纳入监控" % profile)
                continue
            if not os.path.exists(logp):
                continue
            tgt["offset"], drops = scan_file(logp, tgt["offset"])
            if drops:
                handle_drops(profile, dbp, drops, st, args.dry_run)
        save_state(st)
        time.sleep(args.interval)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        wlog("FATAL: 看门狗崩溃\n" + traceback.format_exc())
        raise
