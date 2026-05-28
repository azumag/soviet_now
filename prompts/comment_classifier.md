IMPORTANT: Output ONLY a valid JSON array. No text before or after. No markdown. No explanation.

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
- stream_bug_report: bug reports about the live stream system itself: OBS/eventOverlay, audio/BGM/TTS/game sound, comment fetching/replies, chat workers, dashboard/status displays, monitoring/watchdogs, classifiers, Codex operation, feedback collection, or stream UI counters such as Record showing 0. Question-shaped reports like "無音になってる？" or "BGM聞こえない？" are still stream_bug_report, not general_question. Examples: "音楽がない", "ゲーム音なし", "すぐゲーム音でなくなるね", "無音になってる？", "画面が不調", "Recordも0でいつもと違う". Do NOT use this for gameplay strategy, board placement, scoring, or ordinary game state comments.
- short_reaction: short reactions like "へえ", "なるほど", "それな"
- chitchat: casual conversation not fitting other categories
- other: anything else

Output format (must be valid JSON array):
[{"index":1,"user":"name","comment":"text","category":"cat"},...]

Example output: [{"index":1,"user":"taro","comment":"きらきら星歌って","category":"sing_request"},{"index":2,"user":"hanako","comment":"スコア上がった？","category":"game_status"}]

Comments:
${comments_text}
