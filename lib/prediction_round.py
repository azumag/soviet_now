"""Prediction-only completed-game accounting; never reads improvement/A-B state."""
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile


def record_game(state, game_num, started_at, soviet, russia):
    if state.get('round_version') != 2:
        return
    if started_at <= state['created_at'] or game_num <= state.get('last_game_num', 0):
        return
    if state['games_completed'] >= state['max_games']:
        return
    state['games_completed'] += 1
    state['last_game_num'] = game_num
    state.setdefault('first_game_num', game_num)
    state['best_outcome'] = max(state.get('best_outcome', 0), 2 if soviet else 1 if russia else 0)
    state['russia_created'] = state.get('russia_created', False) or russia or soviet


def main():
    path = Path(sys.argv[1])
    if not path.exists():
        return
    state = json.loads(path.read_text())
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
