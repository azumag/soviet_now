"""Ground radio game references in the committed active game, never a candidate."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

NAMES = {'hanjuku-hero': '半熟英雄', 'sorengame': 'ソ連ゲーム', 'soren91': 'ソ連ゲーム91',
         'nethack': 'NetHack', 'robots': 'Robots'}


def active_game(path: Path) -> str:
    try:
        if path.stat().st_size > 65536:
            return 'unknown'
        state = json.loads(path.read_text(encoding='utf-8'))
        active = state.get('active')
        game = active.get('game') if isinstance(active, dict) else None
        if state.get('phase') == 'ready' and isinstance(game, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', game):
            return game
    except (OSError, ValueError, AttributeError):
        pass
    return 'unknown'


def ground(prompt: str, game: str) -> str:
    if game == 'sorengame':
        return prompt
    name = NAMES.get(game, game) if game != 'unknown' else '確認できていません'
    situation = (f'【状況】現在のメイン画面: {name}。\n'
                 'ソ連ゲームの試合数・前回スコア・盤面・ピースは現在のゲーム状況ではありません。\n'
                 'この入力には現在の戦闘・勝敗・操作の実測がありません。ゲームの進行や作戦を推測して実況せず、今回の雑談テーマを話してください。\n')
    prompt = re.sub(r'【状況】[^\n]*(?:\n(?!【)[^\n]*)*', lambda _: situation.rstrip(), prompt)
    prompt = prompt.replace('自分自身がソ連ゲームをプレイしているプレイヤーでもあります。',
                            'ゲームの担当は現在のメイン画面に従います。')
    # Keep this context even for a corner template without the legacy section.
    return prompt + '\n\n' + situation


def main() -> int:
    prompt_path, canonical = map(Path, sys.argv[1:3])
    game = active_game(canonical)
    prompt_path.write_text(ground(prompt_path.read_text(encoding='utf-8'), game), encoding='utf-8')
    print(game)  # sanitized telemetry only, never the generated prompt
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
