#!/usr/bin/env python3
"""strategy/isolated_runner/selftest_probe.py - issue #35

`run_isolated.py probe` がOS隔離(bwrap/unshare)の中で実際に実行する自己診断
スクリプト。AI生成候補は一切関与しない、host-authoredの信頼済みコード。

サンドボックスの内側から見た uid/gid・ネットワーク到達性・書込み可否を報告する
だけで、それ以上のことは一切しない。結果は引数で渡された出力パス (サンドボックス
内で書込み可能な唯一の場所) に JSON で書く。
"""
from __future__ import annotations

import json
import os
import socket
import sys


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/output/selftest.json"
    result = {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }

    # ネットワーク到達性: unshare-net なら経路自体が無いので即座に失敗するはず。
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("1.1.1.1", 80))
        s.close()
        result["network_blocked"] = False
    except Exception as e:
        result["network_blocked"] = True
        result["network_error"] = f"{type(e).__name__}: {e}"

    # read-only root: / 直下への書込みは拒否されるはず。
    try:
        probe_path = "/__isolated_runner_selftest_write_probe__"
        with open(probe_path, "w") as f:
            f.write("x")
        os.remove(probe_path)
        result["root_write_blocked"] = False
    except Exception as e:
        result["root_write_blocked"] = True
        result["root_write_error"] = f"{type(e).__name__}: {e}"

    # /input は read-only であるはず (存在する場合のみ確認)。
    if os.path.isdir("/input"):
        try:
            probe_path = "/input/__selftest_write_probe__"
            with open(probe_path, "w") as f:
                f.write("x")
            os.remove(probe_path)
            result["input_write_blocked"] = False
        except Exception as e:
            result["input_write_blocked"] = True
            result["input_write_error"] = f"{type(e).__name__}: {e}"

    # /output は書込み可能であるはず。成功フラグをJSONへ含めてから保存する。
    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        result["output_write_ok"] = True
        with open(out_path, "w") as f:
            json.dump(result, f)
    except Exception as e:
        result["output_write_ok"] = False
        result["output_write_error"] = f"{type(e).__name__}: {e}"

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
