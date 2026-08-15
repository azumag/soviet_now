IMPORTANT: Output ONLY a valid JSON array. No text before or after. No markdown. No explanation.

For every comment, also decide whether the comment itself is a genuine English-language comment. Add a boolean field `is_english` to every object. Set it to true only when the comment body is naturally written in English. Viewer names, URLs, game identifiers, ASCII usernames, and repeated Twitch emotes/stamps such as `LUL LUL`, `PogChamp PogChamp`, or `dociaiDoci dociaiDoci` are not English comments. Mixed Japanese text is not English. A short but natural phrase such as "Amazing stream!" or "Well played!" is English.

Classify each comment into ONE of these categories:
- card_gacha: "A obtained B" result messages
- raid: Twitch raid notifications from system/bot accounts such as nightbot. Only classify the actual raid notification as raid. Do NOT classify normal viewer comments after the raid, the raider's own chat messages, or "tombraid" emote reactions as raid.
- subscription: channel subscription messages
- bits: cheer/bits messages
- sing_request: requests to sing a song
- game_question: questions about game rules, strategy, or state
- game_status: comments about current game performance or score
- general_question: questions about non-game topics (people, facts, etc.)
- strategy_advice: comments offering game strategy advice. This includes question-shaped suggestions such as "右に置いた方がよくない？", "nextを見て置くべきでは？", or any advice about board, placement, merging, chain, score, deadline, pieces, hold/next, Russia/Soviet creation strategy.
- comment_advice: comments offering advice on reply style
- stream_bug_report: bug reports about the live stream system itself: OBS/eventOverlay, audio/BGM/TTS/game sound, comment fetching/replies, chat workers, dashboard/status displays, monitoring/watchdogs, classifiers, Codex operation, feedback collection, or stream UI counters such as Record showing 0. Question-shaped or blunt short reports like "無音になってる？", "BGM聞こえない？", or "動いてねえんだわ" are still stream_bug_report, not general_question or chitchat. Examples: "音楽がない", "ゲーム音なし", "すぐゲーム音でなくなるね", "無音になってる？", "動いてねえんだわ", "画面が不調", "Recordも0でいつもと違う". Do NOT use this for gameplay strategy, board placement, scoring, or ordinary game state comments.
- chitchat: casual conversation not fitting other categories, including short reactions like "へえ", "なるほど", "それな". Short reactions must still receive a substantive 3-5 sentence reply; do not create a separate short-reaction category.
- other: anything else

Output format (must be valid JSON array):
[{"index":1,"user":"name","comment":"text","category":"cat","is_english":false},...]

Example output: [{"index":1,"user":"taro","comment":"きらきら星歌って","category":"sing_request","is_english":false},{"index":2,"user":"hanako","comment":"Amazing stream!","category":"chitchat","is_english":true}]

Comments:
${comments_text}
