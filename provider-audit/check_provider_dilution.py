#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v4-flash 掺水检测工具 v2
========================
用途: 对比「金标准」(DeepSeek 官方) 与「待测平台」(opencode 订阅/免费档 等),
      识别待测模型是否「降智」或「掺水」(拿别的模型冒充 / 中转降级)。

方法(3 维度):
  1. 稳定性: 同一道有唯一正确答案的题问 N 次, 统计答对率。
             真模型答对率高且稳定; 掺水/弱模型答对率低且波动。
  2. 判别题集: 多步数学(硬数值)/强格式(JSON)/硬事实(冷门)。
  3. 指纹: 自我认知(不完美), 作为旁证。

用法:
  python check_provider_dilution.py --score N [--model-rhs deepseek-v4-flash] \
      [--gpu-官方-key 从.env自动读] [--oc-key 从.env自动读]
示例:
  # 测 opencode 订阅版 flash, 每题采样 3 次:
  python check_provider_dilution.py
"""
import os, sys, re, time, argparse, json
from openai import OpenAI

VAULT = ".env"  # Hermes 根 .env

# ---------------- 判别题集: (id, prompt, 正则/数值期望, dtype) ----------------
# dtype: num -> 抽数字比对; re -> 正则; json -> 尝试解析
# 设计原则(网研)：用「训练数据难以污染」的新题(定制数/新知识/强格式),
# 掺水/被替代的模型往往在少见题上翻车, 而多-step数值题最能暴露"假接力"。
BENCH = [
    ("math_primes", "100以内的质数一共多少个?只输出一个数字。", r"(?<!\d)(\d{1,3})(?!\d)", 25),
    ("math_rem",    "一个数除以7余3,除以5余2,除以3余1,这个数最小是多少?只输出数字。", r"(?<!\d)(\d{1,3})(?!\d)", 52),
    ("math_apple",  "一篮苹果,每天吃一半还多一个,第5天吃完时剩0个。最初有多少个?只输出数字。", r"(?<!\d)(\d{1,3})(?!\d)", 62),
    ("math_40pct",  "某个数的40%是128,这个数是多少?只输出数字。", r"(?<!\d)(\d{1,3})(?!\d)", 320),
    ("format_json", "只输出一个合法JSON,无其他: {\"name\":\"t\",\"total\":87,\"ratio\":3} 原样返回。", "json", None),
    ("fact_volc",   "珠穆朗玛峰海拔(公认常值)是多少米?只输出数字。", r"(?<!\d)(\d{3,5})(?!\d)", 8848),
    # 新增: 防污染新题(定制数值)
    ("new_seq",     "数列: 3, 7, 15, 31, 63, ? 下一个数是多少?只输出数字。(规律: n*2+1)", r"(?<!\d)(\d{1,5})(?!\d)", 127),
    ("new_mix",     "把 472 和 8 相乘, 再减去 2009, 结果是多少?只输出数字。", r"(?<!\d)(\d{1,6})(?!\d)", 1767),
]

def load_keys():
    keys = {}
    p = os.path.expanduser("~/AppData/Local/hermes/%s" % VAULT)
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    keys[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return keys

def call(base, key, model, prompt, temperature=0.2, max_tokens=600):
    try:
        c = OpenAI(base_url=base, api_key=key, timeout=120)
        r = c.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens)
        return r.choices[0].message.content or ""
    except Exception as e:
        return f"[ERR] {e}"

def extract_num(text):
    m = re.search(r"(?<!\d)(\d{1,6})(?!\d)", text)
    return int(m.group(1)) if m else None

def check_one(prompt, expect, dtype):
    text = call(*CUR_CFG, prompt)
    if text.startswith("[ERR]"):
        return None, text, False       # (数值, 原文, 是否解析到)
    if dtype == "json":
        # JSON 题: 能解析且字段完整 = 对
        try:
            obj = json.loads(text)
            ok = isinstance(obj, dict) and "name" in obj and "total" in obj and "ratio" in obj
            return None, text, ok
        except Exception:
            return None, text, False
    got = extract_num(text)
    if got is None:
        return None, text, False
    ok = (got == expect)
    return got, text, ok

def run_score(cfg, model_tag, trials=3, verbose=True):
    """对一道题采 N 次, 返回答对率。"""
    global CUR_CFG
    CUR_CFG = cfg
    base, key, model = cfg
    print(f"\n{'='*62}\n  [{model_tag}]  model={model}")
    print(f"{'='*62}")
    rows = []
    for pid, prompt, dtype, expect in BENCH:
        oks = 0
        vals = []
        for i in range(trials):
            got, _, parsed = check_one(prompt, expect, dtype)
            if got is not None:
                vals.append(got)
                if got == expect:
                    oks += 1
            elif pid.startswith("format_"):
                # JSON 题没有数值, 用 parsed 计数
                vals.append("valid" if parsed else "invalid")
                if parsed:
                    oks += 1
            else:
                vals.append("?")
        acc = oks / trials
        rows.append((pid, expect, acc, vals))
        print(f"  {pid:12s} expect={expect}  acc={acc:.0%}  sampled={vals}")
    overall = sum(r[2] for r in rows) / len(rows)
    print(f"\n  >>> {model_tag} 综合答对率 = {overall:.0%}  ({len(rows)} 题 x {trials} 次)")
    return overall

def decide(ds_acc, oc_acc, tag_oc):
    """根据金标准与待测的对比给结论。"""
    diff = ds_acc - oc_acc
    print("\n" + "="*62)
    print("  结论判定")
    print("="*62)
    print(f"  金标准(deepseek官方) 答对率: {ds_acc:.0%}")
    print(f"  待测({tag_oc})           答对率: {oc_acc:.0%}")
    print(f"  差距: diff = {diff:+.0%}")

    if ds_acc < 0.5:
        print("\n  [注意] 金标准本身答对率就低(<50%)。"
              "\n  → 这说明判别题对 flash 级模型偏难, 本工具区分力不足,"
              "\n    结论仅供参考——建议换成更简单的判别题再测。")
    if diff >= 0.2:
        print(f"\n  ⚠️ 结论: 待测({tag_oc})显著低于官方 (>={20}%)")
        print("     高度疑似『降智』(能力打了折扣) 或『掺水』(部分请求被别的模型顶替)。")
        print("     建议: 复测 / 换题 / 结合人格指纹 & 长回答质量判断。")
    elif diff >= 0.1:
        print(f"\n  ⚠️ 结论: 待测({tag_oc})略低于官方 (10~20%)")
        print("     有『轻微降智或偶发掺水』的迹象, 但不严重。")
        print("     建议: 拉大采样数(每题7~10次)再确认。")
    elif diff >= 0:
        print(f"\n  ✅ 结论: 待测({tag_oc})与官方基本一致 (相差<10%)")
        print("     无显著降智/掺水迹象, 可以认为是同一模型线路。")
    else:
        print(f"\n  🔺 结论: 待测({tag_oc})答对率甚至高于官方, 无明显掺水。")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--oc-model", default="deepseek-v4-flash",
                    help="待测 opencode 模型名, 如 deepseek-v4-flash / deepseek-v4-flash-free / glm-5.2")
    ap.add_argument("--oc-tag", default="opencode")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()

    k = load_keys()
    ds_key = k.get("DEEPSEEK_API_KEY", "")
    oc_key = k.get("OPENCODE_GO_API_KEY", "") or k.get("OPENCODE_API_KEY", "")

    if not ds_key:
        print("[err] .env 缺 DEEPSEEK_API_KEY"); sys.exit(1)
    if not oc_key:
        print("[err] .env 缺 OPENCODE_*_API_KEY"); sys.exit(1)

    print("#" * 62)
    print("#   v4-flash 掺水/降智检测工具  (采 N 次判别题集)")
    print("#" * 62)

    ds_cfg = ("https://api.deepseek.com/v1", ds_key, "deepseek-chat")
    oc_cfg = ("https://opencode.ai/zen/go/v1", oc_key, args.oc_model)

    print(f"\n采样次数: 每题 {args.trials} 次   | 待测模型: {args.oc_model}")

    print("\n>>> 先用金标准(官方)建立基线...")
    ds_acc = run_score(ds_cfg, "DeepSeek官方(v4-flash)", trials=args.trials)
    print("\n>>> 再测待测平台...")
    oc_acc = run_score(oc_cfg, args.oc_tag, trials=args.trials)
    decide(ds_acc, oc_acc, args.oc_tag)

if __name__ == "__main__":
    main()
