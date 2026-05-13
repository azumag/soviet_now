IMPORTANT: Output ONLY a valid JSON array. No text before or after. No markdown. No explanation.

Classify each comment into ONE of these categories:
- card_gacha: "A obtained B" result messages
- raid: Twitch raid notifications
- subscription: channel subscription messages
- bits: cheer/bits messages
- sing_request: requests to sing a song
- game_question: questions about game rules, strategy, or state
- game_status: comments about current game performance or score
- general_question: questions about non-game topics (people, facts, etc.)
- strategy_advice: comments offering game strategy advice
- comment_advice: comments offering advice on reply style
- short_reaction: short reactions like "へえ", "なるほど", "それな"
- chitchat: casual conversation not fitting other categories
- other: anything else

Output format (must be valid JSON array):
[{"index":1,"user":"name","comment":"text","category":"cat"},...]

Example output: [{"index":1,"user":"taro","comment":"きらきら星歌って","category":"sing_request"},{"index":2,"user":"hanako","comment":"スコア上がった？","category":"game_status"}]

Comments:
${comments_text}
