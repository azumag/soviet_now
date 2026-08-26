"""v739 LOOKAHEAD: decide() の上位候補を「next の 1 手先 (nextNext) の見込み」で再順位付けする 2 手先読み。

設計 (2026-08-26, fable): 各候補について、併合確率 pm (DIRECT 0.96 / NEAR 0.70 / 開いた相方への contact_gap
テーブル 0.83/0.73/0.58/0.32) で「併合した盤面」「しなかった盤面」を作り、nextNext を軽量解析器 (粗い 16 x +
相方 ±0.3、AABB 着地、DIRECT は hit_id + has_obstruction、NEAR は contact_gap<=0.04) で評価した V2 =
pm2 + 0.3·pm2·chain − 高さ罰 の期待値を score + lam·V2 として足す。E2 単調ガード: 期待 2 手併合 (pm1 + V2) が
1 手目の最良より e2_min 以上増えない flip は禁止。純関数 (env / I/O / 時刻なし)、解析呼び出しは max_calls で上限、
例外は全て握って None (decide は 1 手評価に戻る)。実測 (40 試合 1,606 手): 変更 5.8%、期待併合 +0.029/手、
併合喪失 0、新規交差 0、コスト p50 10–27 ms / max 211 ms。"""
import math

COVER_TAGS = ("HIGH_TYPE_COVER_AVOID", "LOW_DROP_HIGH_LANE_COVER_AVOID")
P_GRADE = {"DIRECT": 0.96, "NEAR": 0.70}
PROTECT_TAGS = ("SAME_TYPE_SEED_CONTACT", "ANCHOR_LANE_SEED_CONTACT", "PROBABLE_MERGE_CONTACT")
DEFAULT_CFG = {"k": 8, "lam": 600.0, "margin": 900.0, "lane_bin": 0.4, "e2_min": 0.05, "max_calls": 16,
               "chain_w": 0.3, "height_pen": 0.4, "height_band": 0.8, "exclude_tags": COVER_TAGS + ("AVOID_BLOCK_NEXTNEXT",),
               # 実戦で検証済みの配置軸 (v731/v732/v736) が付いた 1 手目の最良手は、同じタグを持つ候補にしか覆さない
               "protect_tags": PROTECT_TAGS}
_K_CAP = 8
_CALLS_CAP = 16


def _f(v, default):
    try:
        x = float(v)
    except Exception:
        return default
    return x if math.isfinite(x) else default


def p_gap(g):
    if g <= 0.05:
        return 0.83
    if g <= 0.2:
        return 0.73
    if g <= 0.5:
        return 0.58
    if g <= 1.0:
        return 0.32
    return 0.0


def open_ids(pieces, t, bs):
    """type t の相方のうち上端が開いているもの (decide の v736 事前計算と同じ規則)。"""
    out = set()
    rh = bs.seed_horiz_radius(t)
    rt = bs.seed_top_radius(t)
    for sp in pieces:
        if not isinstance(sp, dict) or sp.get("type") != t:
            continue
        sx, sy = _f(sp.get("x"), None), _f(sp.get("y"), None)
        if sx is None or sy is None:
            continue
        top = sy + rt
        ok = True
        for op in pieces:
            if op is sp or not isinstance(op, dict):
                continue
            ox, oy = _f(op.get("x"), None), _f(op.get("y"), None)
            if ox is None or oy is None:
                ok = False
                break
            if abs(ox - sx) <= rh and oy - bs.seed_bottom_radius(op.get("type")) >= top - 0.25:
                ok = False
                break
        if ok:
            out.add(sp.get("id"))
    return out


def p_merge(ch, opens):
    g = ch.get("merge_grade", "NO")
    if g in P_GRADE:
        return P_GRADE[g]
    gaps = [_f(m.get("contact_gap"), 9.9) for m in (ch.get("merges") or []) if isinstance(m, dict) and m.get("id") in opens]
    return p_gap(min(gaps)) if gaps else 0.0


def pick_partner(ch, opens):
    ms = [m for m in (ch.get("merges") or []) if isinstance(m, dict) and (m.get("grade") in ("DIRECT", "NEAR") or m.get("id") in opens)]
    if not ms:
        return None
    ms.sort(key=lambda m: (0 if m.get("grade") == "DIRECT" else 1 if m.get("grade") == "NEAR" else 2, _f(m.get("contact_gap"), 9.9)))
    return ms[0]


def next_board(pieces, ch, nt, merge, opens, ab):
    """候補 ch を打った後の盤面 (併合あり/なし)。併合時は相方位置に type+1 を置き、隣接する同型があれば 1 段連鎖。"""
    new_id = max([p.get("id", 0) or 0 for p in pieces], default=0) + 1
    x = _f(ch.get("x"), 0.0) + _f(ch.get("drift_x"), 0.0)
    y = _f(ch.get("landing_y"), 0.0)
    if merge:
        m = pick_partner(ch, opens)
        if m is not None:
            mx, my0 = _f(m.get("x"), None), _f(m.get("y"), None)
            if mx is None or my0 is None:
                return None, False
            rest = [p for p in pieces if p.get("id") != m.get("id")]
            mt = nt + 1
            my = max(my0, y)
            rest.append({"id": new_id, "type": mt, "x": mx, "y": my, "r": ab.TYPE_RADII.get(mt, 0.5), "angle": 0.0})
            for q in rest:
                if q.get("id") != new_id and q.get("type") == mt and math.hypot(_f(q.get("x"), 99) - mx, _f(q.get("y"), 99) - my) < (_f(q.get("r"), 0.5) + ab.TYPE_RADII.get(mt, 0.5)) * 1.1:
                    rest = [p for p in rest if p.get("id") not in (q.get("id"), new_id)]
                    rest.append({"id": new_id + 1, "type": mt + 1, "x": _f(q.get("x"), mx), "y": max(_f(q.get("y"), my), my), "r": ab.TYPE_RADII.get(mt + 1, 0.5), "angle": 0.0})
                    break
            return rest, True
        return None, False
    return pieces + [{"id": new_id, "type": nt, "x": x, "y": y, "r": ab.TYPE_RADII.get(nt, 0.5), "angle": 0.0}], False


def lite_results(board, nnt, shapes, ab):
    """nextNext 用の軽量解析: 粗い x 格子 + 相方近傍、AABB 着地、DIRECT/NEAR/NO、締切フィールド。"""
    eff = ab.build_deadline_radii(shapes)
    r = ab.TYPE_RADII.get(nnt, 0.5)
    same = [p for p in board if p.get("type") == nnt]
    xs = set(round(-3.0 + i * 0.4, 1) for i in range(16))
    for t in same:
        tx = _f(t.get("x"), None)
        if tx is None:
            continue
        for o in (-0.3, -0.15, 0.0, 0.15, 0.3):
            v = round(tx + o, 2)
            if -3.0 <= v <= 3.0:
                xs.add(v)
    top_r = eff.get(nnt, {}).get("top", r)
    drop_ext = ab._type_deadline_extents(nnt, r, eff)
    out = []
    for x in sorted(xs):
        ly, hit = ab.get_landing_info(x, r, board, eff, nnt)
        lyp = ab.get_deadline_landing_y(x, r, board, eff, nnt)
        merges = []
        best = "NO"
        for t in same:
            gx, gy = ab.polygon_contact_gap(x, ly, drop_ext, t, eff)
            cg = math.hypot(gx, gy)
            if hit == t.get("id"):
                g = "DIRECT" if not ab.has_obstruction(x, r, t, board, eff, nnt) else ("NEAR" if cg <= 0.2 else "NO")
            elif cg <= 0.04:
                g = "NEAR"
            else:
                g = "NO"
            merges.append({"id": t.get("id"), "x": t.get("x"), "y": t.get("y"), "grade": g, "contact_gap": round(cg, 3)})
            if g == "DIRECT":
                best = "DIRECT"
            elif g == "NEAR" and best != "DIRECT":
                best = "NEAR"
        top = lyp + top_r
        mtop = None
        if best != "NO":
            mr = ab.get_type_top_radius(nnt + 1, shapes, eff)
            mtop = min(max(lyp, _f(m.get("y"), lyp)) + mr for m in merges if m["grade"] != "NO")
        risk = max(top, mtop if mtop is not None else top)
        out.append({"x": x, "landing_y": ly, "drift_x": 0.0, "merge_grade": best, "merges": merges, "risk_top_y_after_drop": risk,
                    "crosses_deadline": risk >= ab.DEADLINE_Y, "merge_result_crosses_deadline": (mtop is not None and mtop >= ab.DEADLINE_Y)})
    return out


def v2(board, nnt, shapes, cfg, ab, bs):
    """nextNext の粗い価値: 最良の併合確率 + 連鎖準備 − 高さ危険。安全候補なしは -1。"""
    res = lite_results(board, nnt, shapes, ab)
    safe = [r for r in res if not r.get("crosses_deadline") and not r.get("merge_result_crosses_deadline")]
    if not safe:
        return -1.0
    opens = open_ids(board, nnt, bs)
    pm = 0.0
    best = None
    for r in safe:
        p = p_merge(r, opens)
        if p > pm:
            pm = p
            best = r
    chain = 0.0
    if best is not None and pm > 0:
        m = pick_partner(best, opens)
        if m is not None:
            pt = nnt + 1
            mx, my = _f(m.get("x"), 99), _f(m.get("y"), 99)
            for q in board:
                if q.get("type") == pt and math.hypot(_f(q.get("x"), 99) - mx, _f(q.get("y"), 99) - my) < (_f(q.get("r"), 0.5) + ab.TYPE_RADII.get(pt, 0.5)) * 1.6:
                    chain = cfg["chain_w"]
                    break
    min_risk = min(_f(r.get("risk_top_y_after_drop"), 9.0) for r in safe)
    hr = -cfg["height_pen"] if min_risk >= ab.DEADLINE_Y - cfg["height_band"] else 0.0
    return pm + chain * pm + hr


def rerank(pieces, shapes, nt, nnt, cands, cfg=None):
    """cands = [(x, score, result, reasons)] (decide の 1 手評価)。再順位付けで最良が変わるときだけ
    (x, score, result, reasons) を返す。それ以外 (変更なし / 対象外 / 例外) は None。"""
    try:
        import analyze_board as ab
        from strategy_helpers import board_stats as bs
        c = dict(DEFAULT_CFG)
        if isinstance(cfg, dict):
            c.update(cfg)
        k = max(1, min(_K_CAP, int(c["k"])))
        max_calls = max(1, min(_CALLS_CAP, int(c["max_calls"])))
        lam = _f(c["lam"], 0.0)
        margin = _f(c["margin"], 0.0)
        e2_min = _f(c["e2_min"], 0.05)
        if not isinstance(pieces, list) or not isinstance(cands, list) or len(cands) < 2:
            return None
        try:
            nt = int(nt)
            nnt = int(nnt)
        except Exception:
            return None
        if nt < 1 or nnt < 1 or nnt > 15:
            return None
        board = [p for p in pieces if isinstance(p, dict) and _f(p.get("x"), None) is not None and _f(p.get("y"), None) is not None]
        sh = shapes if isinstance(shapes, dict) else {}
        cands = sorted(cands, key=lambda t: -_f(t[1], -1e9))
        top = _f(cands[0][1], 0.0)
        n = len(board)
        k_eff = k if n <= 24 else max(2, k // 2) if n <= 36 else 2
        excl = tuple(c.get("exclude_tags") or ())
        top_has_excl = any(t in cands[0][3] for t in excl)
        pool = []
        seen = set()
        for cand in cands:
            if _f(cand[1], -1e9) < top - margin or len(pool) >= k_eff:
                break
            res = cand[2]
            if not isinstance(res, dict) or res.get("crosses_deadline") or res.get("merge_result_crosses_deadline"):
                continue
            lane = round((_f(cand[0], 0.0) + _f(res.get("drift_x"), 0.0)) / c["lane_bin"])
            if lane in seen:
                continue
            if not top_has_excl and any(t in cand[3] for t in excl):
                continue
            seen.add(lane)
            pool.append(cand)
        if len(pool) <= 1:
            return None
        opens = open_ids(board, nt, bs)
        calls = 0
        out = []
        for cand in pool:
            x, score, res, reasons = cand
            pm = p_merge(res, opens)
            parts = []
            if pm > 0:
                b, ok = next_board(board, res, nt, True, opens, ab)
                if ok and calls < max_calls:
                    calls += 1
                    parts.append((pm, v2(b, nnt, sh, c, ab, bs)))
                else:
                    pm = 0.0
            if pm < 0.95:
                b, _ = next_board(board, res, nt, False, opens, ab)
                if calls < max_calls:
                    calls += 1
                    parts.append((1.0 - pm, v2(b, nnt, sh, c, ab, bs)))
            if not parts:
                continue
            val = sum(w * v for w, v in parts) / max(1e-9, sum(w for w, _ in parts))
            out.append((_f(score, 0.0) + lam * val, x, score, val, res, reasons, pm))
        if len(out) <= 1:
            return None
        base = max(out, key=lambda t: _f(t[2], -1e9))
        e2b = base[6] + base[3]
        prot = [t for t in (c.get("protect_tags") or ()) if t in base[5]]
        keep = [t for t in out if t is base or (t[6] + t[3] >= e2b + e2_min and all(pt in t[5] for pt in prot))]
        keep.sort(key=lambda t: -t[0])
        pick = keep[0]
        if pick is base or abs(_f(pick[1], 0.0) - _f(base[1], 0.0)) <= 1e-9:
            return None
        if pick[4].get("crosses_deadline") or pick[4].get("merge_result_crosses_deadline"):
            return None
        return (pick[1], pick[2], pick[4], list(pick[5]))
    except Exception:
        return None
