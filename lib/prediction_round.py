"""Prediction-only completed-game accounting; never reads improvement/A-B state."""
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import fcntl
import time


def record_game(state, game_num, started_at, soviet, russia):
    if state.get('round_version') != 2:
        return
    first = state.get('target_first_game')
    eligible = game_num >= first if first else started_at > state['created_at']
    if not eligible or game_num <= state.get('last_game_num', 0):
        return
    if state['games_completed'] >= state['max_games']:
        return
    state['games_completed'] += 1
    state['last_game_num'] = game_num
    state.setdefault('first_game_num', game_num)
    state['best_outcome'] = max(state.get('best_outcome', 0), 2 if soviet else 1 if russia else 0)
    state['russia_created'] = state.get('russia_created', False) or russia or soviet


def display_lines(state, current_game=0):
    if state.get('round_version') != 2:
        return []
    first = state.get('target_first_game') or state.get('first_game_num')
    limit = int(state['max_games'])
    count = int(state.get('games_completed', 0))
    target = f"#{first}〜#{first + limit - 1}" if first else '開始待ち'
    lines = [f"予想対象：{target}｜終了{count}/{limit}｜残り{max(0, limit-count)}試合"]
    if current_game:
        included = first and first <= current_game <= first + limit - 1 and count < limit
        lines.append(f"#{current_game}：今回の予想対象{'（進行中）' if included else '外'}")
    return lines


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def atomic_write(path, value):
    fd, name = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def boundary_command(path, command):
    # Serialize only local publication and game starts, never the Twitch API call.
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / 'prediction_boundary.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = path.parent / 'prediction_game.json'
        if command == 'start':
            atomic_write(marker, dict(game=int(sys.argv[3]), started_at=int(time.time())))
        else:
            state = json.load(sys.stdin)
            try:
                completed = int(Path('game_count.txt').read_text().strip())
            except (OSError, ValueError):
                completed = int(state.get('game_num', 0))
            # Runner fallback covers the first deployment before the next start.
            running = max(int(read_json(marker).get('game', 0)),
                          int(read_json(path.parent / 'main_strategy_runner_active.json').get('game', 0)))
            state['target_first_game'] = max(completed, running) + 1
            state['created_at'] = int(time.time())
            atomic_write(path, state)


def main():
    path = Path(sys.argv[1])
    if sys.argv[2] in ('start', 'publish'):
        boundary_command(path, sys.argv[2])
        return
    if not path.exists():
        return
    state = json.loads(path.read_text())
    if sys.argv[2] == 'display':
        running = read_json(path.parent / 'main_strategy_runner_active.json').get('game', 0)
        print('\n'.join(display_lines(state, int(running))))
        return
    if sys.argv[2] == 'decision':
        key = hashlib.sha256(state['prediction_id'].encode()).hexdigest()
        receipt = path.parent / 'prediction_decisions' / (key + '.json')
        if receipt.exists():
            print(json.loads(receipt.read_text())['outcome'])
            return
        best = state.get('best_outcome', 0)
        if best < 2 and state['games_completed'] < state['max_games']:
            print(-1)
            return
        receipt.parent.mkdir(parents=True, exist_ok=True)
        # Freeze the decision before contacting Twitch; an API retry must not
        # change a concluded round using a later game's result or rollback.
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(prediction_id=state['prediction_id'], outcome=best)))
        os.replace(temporary, receipt)
        print(best)
        return
    record_game(state, int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == 'true', sys.argv[5] == 'true')
    # The game loop is the sole result writer. Worker settlement waits for its
    # regression_check_in_progress fence; atomic replacement protects readers.
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == '__main__':
    main()
