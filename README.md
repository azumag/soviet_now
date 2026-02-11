# Soviet Game Controller

ゲームをファイルから操作する
結果は soviet_now.png に保存される。
soviet_now.png をみて、次の一手を考えて操作せよ。

## コマンドファイル形式

`commands.txt` に以下の形式で記述：

### 形式1: x,y 形式（1行につき1コマンド）

```
640,180
400,200
800,200
```

### 形式2: retry コマンド

ゲームオーバー時にキャンバス中央をクリックしてリトライする。

```
retry
```

## 操作例

```bash
# 単一クリック
echo "640,350" > commands.txt

# 複数クリック
cat > commands.txt << EOF
640,350
400,350
800,350
EOF

# JSONで一括指定
echo '[{"x":640,"y":180},{"x":400,"y":200}]' > commands.txt

# ゲームオーバー時にリトライ
echo "retry" > commands.txt
```

可能な範囲は x が 400-900, yが350固定である。