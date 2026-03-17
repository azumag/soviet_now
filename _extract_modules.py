#!/usr/bin/env python3
"""Extract eloop_lib.sh functions into module files.

Reads eloop_lib.sh, identifies function boundaries, maps functions to
target modules, and writes each module file.
"""
import re
import os
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "eloop_lib.sh"
LIB_RADIO_FILE = BASE_DIR / "lib" / "eloop_radio.sh"

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

# Module headers
MODULE_HEADERS = {
    'core/config.sh': '# core/config.sh - 全定数・パス定義・mkdir初期化',
    'core/helpers.sh': '# core/helpers.sh - log, commands_empty, _trim_log_file 等',
    'core/game_state.sh': '# core/game_state.sh - is_game_over, wait_for_move, send_retry 等',
    'core/version.sh': '# core/version.sh - save_strategy_version, update_best, archive_history',
    'core/phyrogenetic.sh': '# core/phyrogenetic.sh - 進化系統樹の記録・投稿',
    'strategy/ai.sh': '# strategy/ai.sh - spinner, build_prompt, run_cmd, run_ai',
    'strategy/sandbox.sh': '# strategy/sandbox.sh - validate_strategy, create/harvest/destroy_sandbox',
    'strategy/regression.sh': '# strategy/regression.sh - rolling scores, check_regression, rollback候補選定, postmortem生成',
    'strategy/improve.sh': '# strategy/improve.sh - improve_state管理, accumulate, trigger_adaptive_improvement',
    'broadcast/radio_engine.sh': '# broadcast/radio_engine.sh - AI実行ラッパー, パース, サニタイズ, 生成&再生',
    'broadcast/radio_persona.sh': '# broadcast/radio_persona.sh - ペルソナ, 時間帯, 出力ルール, 過去トピック',
    'broadcast/radio_themes.sh': '# broadcast/radio_themes.sh - テーマ選択, マッチング, 使用済みマーク',
    'broadcast/radio_news.sh': '# broadcast/radio_news.sh - ニュース取得・フィルタ・再生',
    'broadcast/radio_factcheck.sh': '# broadcast/radio_factcheck.sh - ファクトチェック, Webグラウンディング',
    'broadcast/radio_corners.sh': '# broadcast/radio_corners.sh - 各コーナー関数 + ディスパッチャー',
    'broadcast/radio_state.sh': '# broadcast/radio_state.sh - ラジオ状態管理, 音声割り込み, キュー',
    'broadcast/radio_celebration.sh': '# broadcast/radio_celebration.sh - ロシア/ソ連建国祝賀, クリップ, チャット投稿',
    'broadcast/comment.sh': '# broadcast/comment.sh - コメント応答生成, コンテキスト構築, advice抽出',
    'broadcast/comment_worker.sh': '# broadcast/comment_worker.sh - player/watcherデーモン管理',
    'broadcast/scheduler.sh': '# broadcast/scheduler.sh - 非同期ジョブスケジュール, Twitchクリップ, audio trigger',
    'infra/cleanup.sh': '# infra/cleanup.sh - PID停止, 子プロセス収集, cleanup_all, cleanup_tmp',
}

# Lib-only functions from lib/eloop_radio.sh (not in eloop_lib.sh)
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


def find_func_end(lines, start_idx):
    """Find the 0-based index of the closing } of a function."""
    depth = 0
    in_heredoc = False
    heredoc_marker = None

    for i in range(start_idx, len(lines)):
        line = lines[i]
        stripped = line.strip()

        if in_heredoc:
            if stripped == heredoc_marker:
                in_heredoc = False
            continue

        # Count braces, ignoring comments and strings
        in_sq = False
        in_dq = False
        j = 0
        line_text = line
        while j < len(line_text):
            ch = line_text[j]
            if in_sq:
                if ch == "'":
                    in_sq = False
                j += 1
                continue
            if in_dq:
                if ch == '\\' and j + 1 < len(line_text):
                    j += 2
                    continue
                if ch == '"':
                    in_dq = False
                j += 1
                continue
            if ch == '#':
                break
            if ch == "'":
                in_sq = True
            elif ch == '"':
                in_dq = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i
            j += 1

        # Check for heredoc start (affects NEXT line)
        if not in_heredoc:
            hd_match = re.search(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?", line)
            if hd_match:
                heredoc_marker = hd_match.group(1)
                in_heredoc = True

    return len(lines) - 1


def extract_functions(lines):
    """Parse function definitions and return list of (name, start_idx, end_idx)."""
    func_pattern = re.compile(r'^([a-zA-Z_][a-zA-Z_0-9]*)\(\)\s*\{')
    functions = []

    for i, line in enumerate(lines):
        m = func_pattern.match(line)
        if m:
            name = m.group(1)
            end_idx = find_func_end(lines, i)
            functions.append((name, i, end_idx))

    return functions


def assign_lines_to_modules(lines, functions, config_end_line=203):
    """Assign every line in the file to a module.

    Returns dict: module_path -> list of (start_idx, end_idx) ranges.
    Ranges are inclusive on both ends.
    """
    module_ranges = defaultdict(list)

    # Config block: lines 0 to config_end_line (0-based)
    module_ranges['core/config.sh'].append((0, config_end_line))

    # Build a sorted list of function definitions
    # For each function, we also want to include its "preamble" (comments before it)
    func_by_start = sorted(functions, key=lambda f: f[1])

    # Create a map: line_idx -> (func_name, is_body)
    # For inter-function gaps, assign to the NEXT function

    # First, mark all function body lines
    body_ranges = {}  # func_name -> (start, end)
    for name, start, end in func_by_start:
        body_ranges[name] = (start, end)

    # Now assign all lines after config to modules
    # Strategy: walk through lines, assigning each to the appropriate module
    current_line = config_end_line + 1  # start after config

    for idx, (name, func_start, func_end) in enumerate(func_by_start):
        module = FUNC_MAP.get(name)
        if module is None:
            print(f"WARNING: Function '{name}' at line {func_start+1} not in FUNC_MAP")
            module = 'UNMAPPED'

        # Include preamble (lines between previous function end and this function start)
        preamble_start = current_line
        if preamble_start < func_start:
            # There's a gap (comments, blank lines) before this function
            module_ranges[module].append((preamble_start, func_start - 1))

        # Include function body
        module_ranges[module].append((func_start, func_end))
        current_line = func_end + 1

    # Handle any trailing lines after the last function
    if current_line < len(lines):
        last_module = FUNC_MAP.get(func_by_start[-1][0], 'UNMAPPED')
        module_ranges[last_module].append((current_line, len(lines) - 1))

    return module_ranges


def write_module(module_path, lines, ranges, header):
    """Write a module file from the given line ranges."""
    output_path = BASE_DIR / module_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content_lines = []
    for start, end in sorted(ranges):
        content_lines.extend(lines[start:end + 1])

    # Build final content
    parts = [header + '\n']

    # For config.sh, include the content as-is (it has its own shebang)
    if module_path == 'core/config.sh':
        parts = content_lines  # config already has the shebang and header
    else:
        parts.append('\n')
        parts.extend(content_lines)

    output_path.write_text(''.join(parts), encoding='utf-8')
    line_count = len(content_lines)
    func_count = sum(1 for line in content_lines if re.match(r'^[a-zA-Z_]\w*\(\)', line))
    print(f"  {module_path}: {line_count} lines, {func_count} functions")


def extract_lib_only_functions(lib_lines):
    """Extract functions from lib/eloop_radio.sh that are not in eloop_lib.sh."""
    func_pattern = re.compile(r'^([a-zA-Z_][a-zA-Z_0-9]*)\(\)\s*\{')
    functions = []

    for i, line in enumerate(lib_lines):
        m = func_pattern.match(line)
        if m:
            name = m.group(1)
            if name in LIB_ONLY_FUNCS:
                end_idx = find_func_end(lib_lines, i)
                functions.append((name, i, end_idx))

    return functions


def append_lib_only_to_modules(lib_lines, lib_functions):
    """Append lib-only functions to their target module files."""
    # Group by module
    module_funcs = defaultdict(list)
    for name, start, end in lib_functions:
        module = LIB_ONLY_FUNCS[name]
        module_funcs[module].append((name, start, end))

    for module, funcs in module_funcs.items():
        output_path = BASE_DIR / module
        with open(output_path, 'a', encoding='utf-8') as f:
            f.write('\n')
            f.write(f'#=== lib/eloop_radio.sh から移行した関数 ===\n\n')
            for name, start, end in sorted(funcs, key=lambda x: x[1]):
                # Include preamble (look back for comments)
                preamble_start = start
                while preamble_start > 0 and lib_lines[preamble_start - 1].strip().startswith('#'):
                    preamble_start -= 1
                # Also include blank line before comments
                if preamble_start > 0 and lib_lines[preamble_start - 1].strip() == '':
                    pass  # don't include blank line

                func_lines = lib_lines[preamble_start:end + 1]
                f.write(''.join(func_lines))
                f.write('\n')

        func_names = [n for n, _, _ in funcs]
        print(f"  {module}: +{len(funcs)} lib-only functions ({', '.join(func_names)})")


def main():
    print("Reading eloop_lib.sh...")
    lines = INPUT_FILE.read_text(encoding='utf-8').splitlines(keepends=True)
    print(f"  {len(lines)} lines")

    print("Parsing function definitions...")
    functions = extract_functions(lines)
    print(f"  {len(functions)} functions found")

    # Verify all functions are mapped
    unmapped = [name for name, _, _ in functions if name not in FUNC_MAP]
    if unmapped:
        print(f"  WARNING: Unmapped functions: {unmapped}")

    print("Assigning lines to modules...")
    module_ranges = assign_lines_to_modules(lines, functions)

    print("Writing module files...")
    for module_path in sorted(module_ranges.keys()):
        if module_path == 'UNMAPPED':
            continue
        ranges = module_ranges[module_path]
        header = MODULE_HEADERS.get(module_path, f'# {module_path}')
        write_module(module_path, lines, ranges, header)

    # Handle lib-only functions from lib/eloop_radio.sh
    if LIB_RADIO_FILE.exists():
        print("\nReading lib/eloop_radio.sh for lib-only functions...")
        lib_lines = LIB_RADIO_FILE.read_text(encoding='utf-8').splitlines(keepends=True)
        lib_functions = extract_lib_only_functions(lib_lines)
        print(f"  {len(lib_functions)} lib-only functions found")

        if lib_functions:
            print("Appending lib-only functions to modules...")
            append_lib_only_to_modules(lib_lines, lib_functions)

    # Report unmapped lines
    if 'UNMAPPED' in module_ranges:
        total_unmapped = sum(e - s + 1 for s, e in module_ranges['UNMAPPED'])
        print(f"\nWARNING: {total_unmapped} unmapped lines")
        for s, e in module_ranges['UNMAPPED']:
            print(f"  Lines {s+1}-{e+1}")

    print("\nDone! Module files created.")

    # Summary
    total_lines = sum(
        sum(e - s + 1 for s, e in ranges)
        for mod, ranges in module_ranges.items()
        if mod != 'UNMAPPED'
    )
    print(f"Total lines extracted: {total_lines}/{len(lines)}")


if __name__ == '__main__':
    main()
