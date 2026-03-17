#!/usr/bin/env python3
"""Extract eloop_lib.sh functions into module files.

Uses a simpler approach: instead of brace counting (which fails on heredocs),
we use function start lines to define boundaries. Each function's block
extends from its definition line to the line before the next function start.
"""
import re
import os
from pathlib import Path
from collections import defaultdict, OrderedDict

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "eloop_lib.sh"
LIB_RADIO_FILE = BASE_DIR / "lib" / "eloop_radio.sh"

CONFIG_END_LINE = 204  # 1-based, inclusive

# Function name -> target module file
FUNC_MAP = {
    # core/helpers.sh
    '_last_score': 'core/helpers.sh',
    '_recent_scores': 'core/helpers.sh',
    '_append_celebration_history': 'core/helpers.sh',
    'commands_empty': 'core/helpers.sh',
    'log': 'core/helpers.sh',
    '_my_pid': 'core/helpers.sh',
    'clear_commands_file': 'core/helpers.sh',
    '_clear_stale_commands_if_any': 'core/helpers.sh',
    '_trim_log_file': 'core/helpers.sh',
    '_strip_ansi': 'core/helpers.sh',
    '_contains_provider_error_text': 'core/helpers.sh',
    '_contains_claude_login_error_text': 'core/helpers.sh',
    # core/game_state.sh
    'is_game_over': 'core/game_state.sh',
    'is_move_state': 'core/game_state.sh',
    'wait_commands_done': 'core/game_state.sh',
    'wait_for_move': 'core/game_state.sh',
    'send_retry': 'core/game_state.sh',
    # core/version.sh
    'save_strategy_version': 'core/version.sh',
    'update_best': 'core/version.sh',
    '_create_twitch_clip': 'core/version.sh',
    'archive_history': 'core/version.sh',
    '_history_gameover_asset_path': 'core/version.sh',
    'archive_gameover_screenshots': 'core/version.sh',
    'recover_strategy_backup': 'core/version.sh',
    # core/phyrogenetic.sh
    'refresh_phyrogenetic_tree': 'core/phyrogenetic.sh',
    '_summarize_strategy_diff_for_phylo': 'core/phyrogenetic.sh',
    '_extract_rollback_analysis_for_phylo': 'core/phyrogenetic.sh',
    'append_phyrogenetic_event': 'core/phyrogenetic.sh',
    '_build_cc_attribution_text': 'core/phyrogenetic.sh',
    '_append_cc_post_log': 'core/phyrogenetic.sh',
    '_append_phyrogenetic_chat_post_log': 'core/phyrogenetic.sh',
    '_post_phyrogenetic_tree_link_to_chat': 'core/phyrogenetic.sh',
    '_post_pending_phyrogenetic_tree_link_to_chat_if_any': 'core/phyrogenetic.sh',
    '_post_cc_text_to_chat': 'core/phyrogenetic.sh',
    '_post_cc_attribution_to_chat': 'core/phyrogenetic.sh',
    '_extract_news_source_name': 'core/phyrogenetic.sh',
    # strategy/ai.sh
    'start_spinner': 'strategy/ai.sh',
    'stop_spinner': 'strategy/ai.sh',
    'build_prompt': 'strategy/ai.sh',
    '_opencode_latest_session_id_for_dir': 'strategy/ai.sh',
    '_run_cmd_session_meta_file': 'strategy/ai.sh',
    '_run_cmd_load_resume_session': 'strategy/ai.sh',
    '_run_cmd_store_resume_session': 'strategy/ai.sh',
    'run_cmd': 'strategy/ai.sh',
    '_build_no_edit_retry_prompt': 'strategy/ai.sh',
    'run_ai': 'strategy/ai.sh',
    # strategy/sandbox.sh
    'validate_strategy': 'strategy/sandbox.sh',
    '_realpath_safe': 'strategy/sandbox.sh',
    '_path_is_under_dir': 'strategy/sandbox.sh',
    'create_sandbox': 'strategy/sandbox.sh',
    'harvest_sandbox': 'strategy/sandbox.sh',
    'destroy_sandbox': 'strategy/sandbox.sh',
    'check_host_integrity': 'strategy/sandbox.sh',
    'validate_strategy_with_helpers': 'strategy/sandbox.sh',
    # strategy/regression.sh
    '_archive_strategy_snapshot_by_hash': 'strategy/regression.sh',
    '_backfill_hash_archive_from_known_versions': 'strategy/regression.sh',
    '_find_strategy_file_by_hash': 'strategy/regression.sh',
    '_refresh_best_strategy_anchor': 'strategy/regression.sh',
    '_has_active_branch': 'strategy/regression.sh',
    '_clear_active_branch': 'strategy/regression.sh',
    '_promote_current_strategy_to_anchor': 'strategy/regression.sh',
    '_branch_transition_after_improve': 'strategy/regression.sh',
    '_is_recently_rejected_for_rollback': 'strategy/regression.sh',
    '_is_blocked_reverse_rollback_pair': 'strategy/regression.sh',
    '_get_rolling_metrics_for_hash': 'strategy/regression.sh',
    '_get_current_strategy_run_metrics': 'strategy/regression.sh',
    '_pick_best_rollback_candidate': 'strategy/regression.sh',
    '_pick_hall_of_fame_rollback_candidate': 'strategy/regression.sh',
    '_prune_hash_archive_by_ranking': 'strategy/regression.sh',
    'update_rolling_scores': 'strategy/regression.sh',
    'check_regression': 'strategy/regression.sh',
    '_write_rollback_analysis_file': 'strategy/regression.sh',
    '_write_rollback_postmortem_context_file': 'strategy/regression.sh',
    '_generate_rollback_postmortem_with_ai': 'strategy/regression.sh',
    'start_rollback_postmortem_worker': 'strategy/regression.sh',
    # strategy/improve.sh
    '_read_improve_state': 'strategy/improve.sh',
    '_write_improve_state': 'strategy/improve.sh',
    'check_and_harvest_improvement': 'strategy/improve.sh',
    'accumulate_game_data': 'strategy/improve.sh',
    '_read_accumulated_data': 'strategy/improve.sh',
    '_clear_accumulated_data': 'strategy/improve.sh',
    '_reset_current_strategy_run': 'strategy/improve.sh',
    '_seed_current_strategy_run_from_rolling': 'strategy/improve.sh',
    '_update_current_strategy_run': 'strategy/improve.sh',
    'record_completed_game_for_adaptive_improvement': 'strategy/improve.sh',
    '_start_improvement_job': 'strategy/improve.sh',
    'trigger_adaptive_improvement': 'strategy/improve.sh',
    # broadcast/radio_engine.sh
    '_run_opencode_radio': 'broadcast/radio_engine.sh',
    '_run_opencode_comment': 'broadcast/radio_engine.sh',
    '_run_claude_comment_with_model': 'broadcast/radio_engine.sh',
    '_run_claude_comment': 'broadcast/radio_engine.sh',
    '_run_claude_radio_with_model': 'broadcast/radio_engine.sh',
    '_run_claude_radio': 'broadcast/radio_engine.sh',
    '_clean_comment_talk': 'broadcast/radio_engine.sh',
    '_is_valid_comment_talk': 'broadcast/radio_engine.sh',
    '_is_valid_radio_talk': 'broadcast/radio_engine.sh',
    '_radio_extract_fact_check_script': 'broadcast/radio_engine.sh',
    '_radio_extract_fact_check_issues': 'broadcast/radio_engine.sh',
    '_radio_cleanup_fact_checked_text': 'broadcast/radio_engine.sh',
    '_radio_extract_prompt_section_value': 'broadcast/radio_engine.sh',
    '_radio_extract_prompt_section_block': 'broadcast/radio_engine.sh',
    '_radio_compact_fact_check_context': 'broadcast/radio_engine.sh',
    '_radio_parse_output_to_files': 'broadcast/radio_engine.sh',
    '_radio_dedup_text': 'broadcast/radio_engine.sh',
    '_sanitize_onair_text': 'broadcast/radio_engine.sh',
    '_normalize_radio_tone': 'broadcast/radio_engine.sh',
    '_ensure_corner_announce': 'broadcast/radio_engine.sh',
    '_ensure_radio_intro': 'broadcast/radio_engine.sh',
    '_radio_generate_and_play': 'broadcast/radio_engine.sh',
    # broadcast/radio_persona.sh
    '_radio_time_context': 'broadcast/radio_persona.sh',
    '_refresh_radio_intro_for_playback_file': 'broadcast/radio_persona.sh',
    '_radio_persona_block': 'broadcast/radio_persona.sh',
    '_radio_output_rules': 'broadcast/radio_persona.sh',
    '_radio_past_topics_block': 'broadcast/radio_persona.sh',
    # broadcast/radio_themes.sh
    '_radio_theme_key_from_body': 'broadcast/radio_themes.sh',
    '_radio_theme_recent_match_mode': 'broadcast/radio_themes.sh',
    '_radio_mark_theme_used': 'broadcast/radio_themes.sh',
    '_pick_radio_theme': 'broadcast/radio_themes.sh',
    # broadcast/radio_news.sh
    '_news_title_key': 'broadcast/radio_news.sh',
    '_news_topic_key': 'broadcast/radio_news.sh',
    '_filter_unread_news_blocks': 'broadcast/radio_news.sh',
    '_resolve_selected_news_title': 'broadcast/radio_news.sh',
    '_news_source_name_for_title': 'broadcast/radio_news.sh',
    '_news_url_hash_for_title': 'broadcast/radio_news.sh',
    '_news_source_key_from_name': 'broadcast/radio_news.sh',
    '_append_news_read_source': 'broadcast/radio_news.sh',
    '_append_news_read_url_hash': 'broadcast/radio_news.sh',
    '_prepare_news_prompt_blocks': 'broadcast/radio_news.sh',
    '_random_pick_news_block': 'broadcast/radio_news.sh',
    '_news_source_balance_hint': 'broadcast/radio_news.sh',
    # broadcast/radio_factcheck.sh
    '_radio_extract_grounding_query': 'broadcast/radio_factcheck.sh',
    '_radio_fetch_web_grounding': 'broadcast/radio_factcheck.sh',
    '_radio_should_fact_check': 'broadcast/radio_factcheck.sh',
    '_radio_compact_text_len': 'broadcast/radio_factcheck.sh',
    '_radio_fact_check_length_ok': 'broadcast/radio_factcheck.sh',
    '_radio_fact_check_style_reason': 'broadcast/radio_factcheck.sh',
    '_radio_fact_check_body': 'broadcast/radio_factcheck.sh',
    # broadcast/radio_corners.sh
    'start_radio_corner_theme': 'broadcast/radio_corners.sh',
    'start_radio_corner_news': 'broadcast/radio_corners.sh',
    'start_radio_corner_strategy': 'broadcast/radio_corners.sh',
    'start_radio_corner_rollback': 'broadcast/radio_corners.sh',
    'start_radio_corner_weather': 'broadcast/radio_corners.sh',
    'start_radio_corner_fortune': 'broadcast/radio_corners.sh',
    'start_radio_corner_market': 'broadcast/radio_corners.sh',
    'start_radio_corner_dinner': 'broadcast/radio_corners.sh',
    'start_radio_corner_deals': 'broadcast/radio_corners.sh',
    'start_radio_corner_survival': 'broadcast/radio_corners.sh',
    'start_radio_corner_rakugo': 'broadcast/radio_corners.sh',
    'start_radio_corner_breakfast': 'broadcast/radio_corners.sh',
    'start_radio_corner_lunch': 'broadcast/radio_corners.sh',
    'start_radio_corner_devil_dict': 'broadcast/radio_corners.sh',
    'start_radio_corner_soviet_quiz': 'broadcast/radio_corners.sh',
    'start_radio_corner_parallel_news': 'broadcast/radio_corners.sh',
    'start_radio_corner_bluegrass': 'broadcast/radio_corners.sh',
    'start_radio_corner_redefine': 'broadcast/radio_corners.sh',
    'start_radio_corner_soviet_lifehack': 'broadcast/radio_corners.sh',
    'start_radio_corner_world_dinner': 'broadcast/radio_corners.sh',
    'start_radio_corner_night_snack': 'broadcast/radio_corners.sh',
    # broadcast/radio_state.sh
    '_radio_gc_stale_state': 'broadcast/radio_state.sh',
    '_radio_set_state': 'broadcast/radio_state.sh',
    '_radio_clear_state': 'broadcast/radio_state.sh',
    '_interrupt_current_audio_playback': 'broadcast/radio_state.sh',
    '_play_priority_audio_file': 'broadcast/radio_state.sh',
    '_cancel_russia_celebration_worker': 'broadcast/radio_state.sh',
    '_radio_mark_done': 'broadcast/radio_state.sh',
    '_enqueue_deferred_radio_talk': 'broadcast/radio_state.sh',
    '_run_jiji_corner_guarded': 'broadcast/radio_state.sh',
    '_play_deferred_radio_queue_once': 'broadcast/radio_state.sh',
    # broadcast/radio_celebration.sh
    'generate_russia_celebration': 'broadcast/radio_celebration.sh',
    'generate_soviet_celebration': 'broadcast/radio_celebration.sh',
    # broadcast/comment.sh
    '_kill_comment_gen': 'broadcast/comment.sh',
    'get_comment_backlog_counts': 'broadcast/comment.sh',
    'is_comment_backlog_high': 'broadcast/comment.sh',
    '_comment_has_manual_claude_trigger': 'broadcast/comment.sh',
    '_strip_comment_control_prefixes': 'broadcast/comment.sh',
    '_comment_should_use_claude_only': 'broadcast/comment.sh',
    '_is_recent_comment_batch_processed': 'broadcast/comment.sh',
    '_is_comment_batch_inflight': 'broadcast/comment.sh',
    '_mark_comment_batch_inflight': 'broadcast/comment.sh',
    '_clear_comment_batch_inflight': 'broadcast/comment.sh',
    '_mark_comment_batch_processed': 'broadcast/comment.sh',
    '_filter_already_processed_comment_lines': 'broadcast/comment.sh',
    '_record_processed_comment_lines': 'broadcast/comment.sh',
    '_format_comment_batch_context': 'broadcast/comment.sh',
    '_remember_spoken_comment': 'broadcast/comment.sh',
    '_current_playing_comment_file': 'broadcast/comment.sh',
    '_build_recent_spoken_comment_context': 'broadcast/comment.sh',
    '_build_comment_followup_hints': 'broadcast/comment.sh',
    '_build_comment_game_context': 'broadcast/comment.sh',
    '_build_comment_celebration_history_context': 'broadcast/comment.sh',
    '_extract_strategy_advice_from_comments': 'broadcast/comment.sh',
    '_append_strategy_advice_item': 'broadcast/comment.sh',
    'generate_comment_response': 'broadcast/comment.sh',
    '_cleanup_comment_gen_worker': 'broadcast/comment.sh',
    # broadcast/comment_worker.sh
    '_recover_orphan_comment_playing_files': 'broadcast/comment_worker.sh',
    '_play_comment_queue': 'broadcast/comment_worker.sh',
    '_is_comment_worker_healthy': 'broadcast/comment_worker.sh',
    'start_comment_player': 'broadcast/comment_worker.sh',
    'stop_comment_player': 'broadcast/comment_worker.sh',
    'start_comment_watcher': 'broadcast/comment_worker.sh',
    'stop_comment_watcher': 'broadcast/comment_worker.sh',
    # broadcast/scheduler.sh
    'fetch_and_play_news': 'broadcast/scheduler.sh',
    '_build_manual_strategy_diff': 'broadcast/scheduler.sh',
    '_dispatch_manual_audio_trigger': 'broadcast/scheduler.sh',
    'process_external_audio_triggers': 'broadcast/scheduler.sh',
    'start_random_radio_corner': 'broadcast/scheduler.sh',
    'schedule_nonessential_audio_jobs': 'broadcast/scheduler.sh',
    # infra/cleanup.sh
    '_stop_pid_with_fallback': 'infra/cleanup.sh',
    '_collect_descendant_pids': 'infra/cleanup.sh',
    '_is_audio_playback_process': 'infra/cleanup.sh',
    '_stop_loop_descendants': 'infra/cleanup.sh',
    'cleanup_all': 'infra/cleanup.sh',
    'cleanup_tmp_files': 'infra/cleanup.sh',
}

MODULE_HEADERS = {
    'core/config.sh': '# core/config.sh - 全定数・パス定義・mkdir初期化\n',
    'core/helpers.sh': '# core/helpers.sh - log, commands_empty, _trim_log_file 等\n',
    'core/game_state.sh': '# core/game_state.sh - is_game_over, wait_for_move, send_retry 等\n',
    'core/version.sh': '# core/version.sh - save_strategy_version, update_best, archive_history\n',
    'core/phyrogenetic.sh': '# core/phyrogenetic.sh - 進化系統樹の記録・投稿\n',
    'strategy/ai.sh': '# strategy/ai.sh - spinner, build_prompt, run_cmd, run_ai\n',
    'strategy/sandbox.sh': '# strategy/sandbox.sh - validate_strategy, create/harvest/destroy_sandbox\n',
    'strategy/regression.sh': '# strategy/regression.sh - rolling scores, check_regression, rollback候補選定, postmortem生成\n',
    'strategy/improve.sh': '# strategy/improve.sh - improve_state管理, accumulate, trigger_adaptive_improvement\n',
    'broadcast/radio_engine.sh': '# broadcast/radio_engine.sh - AI実行ラッパー, パース, サニタイズ, 生成&再生\n',
    'broadcast/radio_persona.sh': '# broadcast/radio_persona.sh - ペルソナ, 時間帯, 出力ルール, 過去トピック\n',
    'broadcast/radio_themes.sh': '# broadcast/radio_themes.sh - テーマ選択, マッチング, 使用済みマーク\n',
    'broadcast/radio_news.sh': '# broadcast/radio_news.sh - ニュース取得・フィルタ・再生\n',
    'broadcast/radio_factcheck.sh': '# broadcast/radio_factcheck.sh - ファクトチェック, Webグラウンディング\n',
    'broadcast/radio_corners.sh': '# broadcast/radio_corners.sh - 各コーナー関数 + ディスパッチャー\n',
    'broadcast/radio_state.sh': '# broadcast/radio_state.sh - ラジオ状態管理, 音声割り込み, キュー\n',
    'broadcast/radio_celebration.sh': '# broadcast/radio_celebration.sh - ロシア/ソ連建国祝賀, クリップ, チャット投稿\n',
    'broadcast/comment.sh': '# broadcast/comment.sh - コメント応答生成, コンテキスト構築, advice抽出\n',
    'broadcast/comment_worker.sh': '# broadcast/comment_worker.sh - player/watcherデーモン管理\n',
    'broadcast/scheduler.sh': '# broadcast/scheduler.sh - 非同期ジョブスケジュール, Twitchクリップ, audio trigger\n',
    'infra/cleanup.sh': '# infra/cleanup.sh - PID停止, 子プロセス収集, cleanup_all, cleanup_tmp\n',
}

LIB_ONLY_FUNCS = {
    '_radio_fetch_theme_grounding_context': 'broadcast/radio_themes.sh',
    '_pick_soviet_theme': 'broadcast/radio_themes.sh',
    'start_radio_corner_soviet': 'broadcast/radio_corners.sh',
    'start_radio_corner_recap': 'broadcast/radio_corners.sh',
    '_filter_unread_jiji_blocks': 'broadcast/radio_corners.sh',
    '_run_opencode_jiji_research': 'broadcast/radio_corners.sh',
    'start_radio_corner_jiji': 'broadcast/radio_corners.sh',
    '_legacy_fetch_and_play_news': 'broadcast/scheduler.sh',
    '_legacy_start_random_radio_corner': 'broadcast/scheduler.sh',
    '_legacy_schedule_nonessential_audio_jobs': 'broadcast/scheduler.sh',
}


def find_func_starts(lines):
    """Find all function definition lines. Returns [(name, 0-based line index)]."""
    func_pattern = re.compile(r'^([a-zA-Z_][a-zA-Z_0-9]*)\(\)\s*\{')
    funcs = []
    for i, line in enumerate(lines):
        m = func_pattern.match(line)
        if m:
            funcs.append((m.group(1), i))
    return funcs


def find_preamble_start(lines, func_line_idx, floor_idx):
    """Find where the preamble (section comments) starts above a function.

    Look backward from func_line_idx for consecutive comment/blank lines.
    Stop at floor_idx (previous function's definition line or config end).
    """
    i = func_line_idx - 1
    preamble = func_line_idx
    while i >= floor_idx:
        stripped = lines[i].strip()
        if stripped == '' or stripped.startswith('#'):
            preamble = i
            i -= 1
        else:
            break
    return preamble


def main():
    print("Reading eloop_lib.sh...")
    lines = INPUT_FILE.read_text(encoding='utf-8').splitlines(keepends=True)
    print(f"  {len(lines)} lines")

    print("Parsing function starts...")
    func_starts = find_func_starts(lines)
    print(f"  {len(func_starts)} functions found")

    # Check for unmapped functions
    unmapped = [name for name, _ in func_starts if name not in FUNC_MAP]
    if unmapped:
        print(f"  WARNING: Unmapped functions: {unmapped}")

    # Sort by line number
    func_starts_sorted = sorted(func_starts, key=lambda x: x[1])

    # Compute blocks: each function gets its preamble + body
    # Preamble = section comments/blank lines above the function definition
    # Body = everything from function definition to just before next function's preamble

    # Step 1: find preamble starts for all functions
    preamble_starts = []
    for idx, (name, line_idx) in enumerate(func_starts_sorted):
        if idx == 0:
            floor = CONFIG_END_LINE  # 0-based: line 204 = index 203
        else:
            floor = func_starts_sorted[idx - 1][1] + 1
        ps = find_preamble_start(lines, line_idx, floor)
        preamble_starts.append(ps)

    # Step 2: assign line ranges to functions
    # Each function's block: [preamble_start, next_function's preamble_start - 1]
    module_lines = defaultdict(list)  # module -> list of line strings

    # Config block: lines 0 to CONFIG_END_LINE-1 (0-based)
    config_lines = lines[0:CONFIG_END_LINE]
    module_lines['core/config.sh'] = list(config_lines)

    # Gap between config and first function's preamble
    first_preamble = preamble_starts[0] if preamble_starts else CONFIG_END_LINE
    if first_preamble > CONFIG_END_LINE:
        gap = lines[CONFIG_END_LINE:first_preamble]
        first_module = FUNC_MAP.get(func_starts_sorted[0][0], 'UNMAPPED')
        module_lines[first_module].extend(gap)

    for idx, (name, line_idx) in enumerate(func_starts_sorted):
        module = FUNC_MAP.get(name, 'UNMAPPED')

        block_start = preamble_starts[idx]
        if idx < len(func_starts_sorted) - 1:
            block_end = preamble_starts[idx + 1]
        else:
            block_end = len(lines)

        block = lines[block_start:block_end]
        module_lines[module].extend(block)

    # Write module files
    print("\nWriting module files...")
    total_written = 0
    for module_path in sorted(module_lines.keys()):
        if module_path == 'UNMAPPED':
            continue

        output_path = BASE_DIR / module_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        content_lines = module_lines[module_path]

        # Build file content
        header = MODULE_HEADERS.get(module_path, f'# {module_path}\n')

        if module_path == 'core/config.sh':
            # Config already has shebang + header comments
            content = ''.join(content_lines)
        else:
            content = header + '\n' + ''.join(content_lines)

        output_path.write_text(content, encoding='utf-8')

        func_count = sum(1 for line in content_lines
                         if re.match(r'^[a-zA-Z_]\w*\(\)', line))
        line_count = len(content_lines)
        total_written += line_count
        print(f"  {module_path}: {line_count} lines, {func_count} functions")

    # Handle lib-only functions from lib/eloop_radio.sh
    if LIB_RADIO_FILE.exists():
        print("\nProcessing lib/eloop_radio.sh for lib-only functions...")
        lib_lines = LIB_RADIO_FILE.read_text(encoding='utf-8').splitlines(keepends=True)
        lib_func_starts = find_func_starts(lib_lines)
        lib_func_starts_sorted = sorted(
            [(n, i) for n, i in lib_func_starts if n in LIB_ONLY_FUNCS],
            key=lambda x: x[1]
        )

        # Also get ALL func starts for boundary computation
        all_lib_starts = sorted(lib_func_starts, key=lambda x: x[1])
        all_lib_line_indices = [i for _, i in all_lib_starts]

        # Group lib-only functions by module
        lib_module_funcs = defaultdict(list)
        for name, line_idx in lib_func_starts_sorted:
            module = LIB_ONLY_FUNCS[name]

            # Find preamble
            # Floor: previous function in the lib file
            pos = all_lib_line_indices.index(line_idx)
            floor = all_lib_starts[pos - 1][1] + 1 if pos > 0 else 0
            ps = find_preamble_start(lib_lines, line_idx, floor)

            # Find end: next function's preamble start in lib file
            if pos < len(all_lib_starts) - 1:
                next_line = all_lib_starts[pos + 1][1]
                next_ps = find_preamble_start(lib_lines, next_line, line_idx + 1)
                block_end = next_ps
            else:
                block_end = len(lib_lines)

            block = lib_lines[ps:block_end]
            lib_module_funcs[module].append((name, block))

        for module, funcs in sorted(lib_module_funcs.items()):
            output_path = BASE_DIR / module
            with open(output_path, 'a', encoding='utf-8') as f:
                f.write('\n#=== lib/eloop_radio.sh から移行した関数 ===\n\n')
                for name, block in funcs:
                    f.write(''.join(block))
            func_names = [n for n, _ in funcs]
            print(f"  {module}: +{len(funcs)} lib-only ({', '.join(func_names)})")

    # Handle unmapped
    if 'UNMAPPED' in module_lines:
        um_lines = module_lines['UNMAPPED']
        um_funcs = sum(1 for l in um_lines if re.match(r'^[a-zA-Z_]\w*\(\)', l))
        print(f"\nWARNING: {len(um_lines)} unmapped lines ({um_funcs} functions)")

    print(f"\nTotal lines written: {total_written}/{len(lines)}")

    # Verify: count all functions in output files
    all_output_funcs = set()
    for module_path in module_lines:
        if module_path == 'UNMAPPED':
            continue
        for line in module_lines[module_path]:
            m = re.match(r'^([a-zA-Z_]\w*)\(\)', line)
            if m:
                all_output_funcs.add(m.group(1))

    original_funcs = {name for name, _ in func_starts}
    missing = original_funcs - all_output_funcs
    extra = all_output_funcs - original_funcs
    if missing:
        print(f"WARNING: Missing functions in output: {missing}")
    if extra:
        print(f"INFO: Extra functions in output: {extra}")

    print(f"Functions: {len(all_output_funcs)} in output vs {len(original_funcs)} in original")


if __name__ == '__main__':
    main()
