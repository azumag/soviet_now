#!/bin/bash
# batch_test.sh - Run N games without AI improvement for performance testing

NUM_GAMES=${1:-10} # Default to 10 games
RESULTS_DIR="tmp/batch_results"

mkdir -p "$RESULTS_DIR"

echo "=== Batch Test: v147 Strategy ==="
echo "Running $NUM_GAMES games..."
echo "Results will be saved to $RESULTS_DIR/"

# Track scores
TOTAL_SCORE=0
BEST_SCORE=0
WORST_SCORE=999999
GAME_NUM=1

for i in $(seq 1 $NUM_GAMES); do
	echo ""
	echo "=== Game $i/$NUM_GAMES ==="

	# Run one game using strategy_runner.py
	# It outputs JSON with score and turns at GAMEOVER
	RESULT=$(python3 strategy_runner.py)

	# Extract score from the JSON output
	SCORE=$(echo "$RESULT" | grep -o '"score":[0-9]*' | cut -d: -f2)
	TURNS=$(echo "$RESULT" | grep -o '"turns":[0-9]*' | cut -d: -f2)

	echo "Score: $SCORE (turns: $TURNS)"

	# Save result
	echo "$RESULT" >"$RESULTS_DIR/game_${GAME_NUM}_score${SCORE}.json"

	# Update statistics
	if [ -n "$SCORE" ]; then
		TOTAL_SCORE=$((TOTAL_SCORE + SCORE))
		if [ $SCORE -gt $BEST_SCORE ]; then
			BEST_SCORE=$SCORE
		fi
		if [ $SCORE -lt $WORST_SCORE ]; then
			WORST_SCORE=$SCORE
		fi
	fi

	GAME_NUM=$((GAME_NUM + 1))

	# Small delay between games
	sleep 2
done

# Calculate average
if [ $NUM_GAMES -gt 0 ]; then
	AVG_SCORE=$((TOTAL_SCORE / NUM_GAMES))
else
	AVG_SCORE=0
fi

echo ""
echo "=== Batch Test Complete ==="
echo "Games played: $NUM_GAMES"
echo "Average score: $AVG_SCORE"
echo "Best score: $BEST_SCORE"
echo "Worst score: $WORST_SCORE"
echo ""
echo "Results saved to $RESULTS_DIR/"
