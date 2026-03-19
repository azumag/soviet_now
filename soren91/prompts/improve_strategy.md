# Strategy Improvement Prompt

You are 同志AI — improving the strategy for a Suika-style (watermelon game / ソ連ゲーム) physics puzzle game.

## Game Rules
- Pieces drop from the top and stack on the board
- When two pieces of the same type touch, they merge into the next larger type
- Board X range: [-3.0, +3.0] (walls at ±3.5)
- Board Y range: [-5.0 floor, +3.32 deadline]
- If pieces stack past the deadline (~y=2.5), game over
- Goal: maximize score through efficient merges and chain reactions

## Piece Types (1-15, ascending size)
Each type has a fixed radius. Type N + Type N → Type N+1.
The largest achievable type is 15 (ソ連/Russia).

## Strategy Interface
```javascript
export function decide(boardState) {
  // boardState: {
  //   pieces: [{type, x, y, r}],   // 検出されたピース (国ピースのみ、おじゃまは除外)
  //   next: {type, r},              // 次にドロップするピース
  //   score: number,
  //   confidence: number,
  //   garbage: { ratio, height, pixelCount }  // おじゃまブロック情報
  //     ratio: ボード内のおじゃまの割合 (0-1)
  //     height: おじゃまの最高到達Y座標 (ゲーム座標, 高い=危険)
  // }
  // Returns: { x: number [-3.0, 3.0], reason: string }
}
```

## Garbage Blocks (おじゃまブロック)
- Gray blocks sent by opponents
- They stack from the bottom of the board
- They disappear when you merge pieces (create a country)
- When garbage.ratio > 0.5, the board is mostly garbage — prioritize merges to clear it
- garbage.height indicates how high the garbage has reached

## Key Principles
1. **Merge Priority**: Always try to place next to same-type pieces for merges
2. **Height Management**: Keep the board low, especially near deadline
3. **Balance**: Distribute weight evenly left-right to prevent toppling
4. **Chain Potential**: Position pieces so merges cascade (merged piece near same type)
5. **Grouping**: Keep same-type pieces together for future merge opportunities

## Important Constraints
- Input comes from screenshot analysis (imperfect - pieces may be misclassified)
- Strategy should be noise-tolerant
- Keep the code simple and readable
- Do NOT import external modules - pure logic only
- You MUST only output strategy.mjs code. Do NOT modify any other files
- The function signature `export function decide(boardState)` MUST be preserved
- Return value MUST be `{ x: number, reason: string }` where x is in [-3.0, 3.0]
- Do NOT use async/await, fetch, fs, or any side effects - pure computation only
