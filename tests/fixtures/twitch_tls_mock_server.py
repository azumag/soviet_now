#!/usr/bin/env python3
"""tests/fixtures/twitch_tls_mock_server.py - docich issue #38 用のローカル mock TLS
IRC サーバ。

twitch_chat_daemon.sh の TLS transport (openssl s_client) が接続する相手として
テストからのみ使う。実際の Twitch サーバへは一切接続しない。TLSハンドシェイクを
本物のIRCサーバのように完了させ、--script で指定したテキストファイルの各行を
CRLF付きでそのままクライアントへ送る(内容の妥当性はチェックしない。呼び出し側の
テストが好きなROOMSTATE/PRIVMSG/PING行を仕込める)。

Usage:
  twitch_tls_mock_server.py --port 0 --cert c.pem --key k.pem --script s.txt \
      [--send-delay-ms 10] [--accept-count 1] [--ready-file /path/to/ready]

--port 0 で OS に空きポートを選ばせ、実際にbindしたポート番号を --ready-file に
書き出す(並行実行での固定ポート衝突を避けるため。呼び出し側はready-fileが
出現するまで待ってからポート番号を読む)。
"""
import argparse
import socket
import ssl
import sys
import threading
import time


def _drain(conn):
    # クライアントが送ってくる PASS/CAP/NICK/JOIN/PONG 等は内容を検証せず読み捨てる。
    # 読み捨てないとクライアント側の書き込みがブロックしうるため必須。
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cert", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--send-delay-ms", type=int, default=10)
    ap.add_argument("--accept-count", type=int, default=1)
    ap.add_argument("--ready-file", default="")
    ap.add_argument("--linger-ms", type=int, default=300)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        lines = [ln.rstrip("\n").rstrip("\r") for ln in f]
    lines = [ln for ln in lines if ln != ""]

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=args.cert, keyfile=args.key)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(5)
    srv.settimeout(30)

    bound_port = srv.getsockname()[1]
    if args.ready_file:
        with open(args.ready_file, "w", encoding="utf-8") as rf:
            rf.write(f"{bound_port}\n")

    accepted = 0
    while accepted < args.accept_count:
        try:
            conn, _addr = srv.accept()
        except socket.timeout:
            break
        accepted += 1
        # ハンドシェイクが失敗するケース(クライアントが証明書検証エラーで異常切断
        # する等)では、TLSレベルの完了通知が来ずソケットレベルのrecv()が返らない
        # ことがある。ハングしてプロセスが残留しないよう必ずタイムアウトを設定する。
        conn.settimeout(10)
        try:
            tls_conn = ctx.wrap_socket(conn, server_side=True)
        except (ssl.SSLError, OSError) as e:
            sys.stderr.write(f"[mock-server] handshake failed: {e}\n")
            try:
                conn.close()
            except Exception:
                pass
            continue

        t = threading.Thread(target=_drain, args=(tls_conn,), daemon=True)
        t.start()
        try:
            for ln in lines:
                tls_conn.sendall((ln + "\r\n").encode("utf-8"))
                if args.send_delay_ms > 0:
                    time.sleep(args.send_delay_ms / 1000.0)
            if args.linger_ms > 0:
                time.sleep(args.linger_ms / 1000.0)
        except Exception as e:
            sys.stderr.write(f"[mock-server] send failed: {e}\n")
        finally:
            try:
                tls_conn.close()
            except Exception:
                pass

    srv.close()


if __name__ == "__main__":
    main()
