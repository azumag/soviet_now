import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest import mock

from lib.stream_title_public import (
    DEFAULT_FALLBACK,
    ViewerCandidate,
    adoptable_candidate,
    choose_body,
    compose_title,
    looks_internal,
    parse_viewer_candidate,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def candidate_text(
    *,
    title="AIの取引コーナーからゲームへ、画面切替を改善",
    rationale="画面復帰を運用で確認",
    status="deployed",
    generated_at=None,
    valid_scope=None,
) -> str:
    lines = [f"viewer_title: {title}"]
    if rationale is not None:
        lines.append(f"rationale: {rationale}")
    if status is not None:
        lines.append(f"status: {status}")
    lines.append(f"generated_at: {generated_at if generated_at is not None else now_iso()}")
    if valid_scope is not None:
        lines.append(f"valid_scope: {valid_scope}")
    return "\n".join(lines) + "\n"


class StreamTitlePublicTest(unittest.TestCase):
    def candidate_file(self, root: Path, body: str) -> str:
        path = root / "viewer_title.md"
        path.write_text(body, encoding="utf-8")
        return str(path)

    # --- 内部識別子の検出 ---

    def test_internal_development_logs_are_rejected(self):
        samples = [
            "PR770をmainへマージ",
            "PR #770 を main にマージ、VM反映と実機状態を確認",
            "docich#770 の参照更新",
            "5ec6fdd88909aa554e02859170b4d8d711be0db4 をデプロイ",
            "tests/test_title_day.py のCI成功",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(looks_internal(sample))

    def test_internal_logs_adjacent_to_japanese_are_rejected(self):
        # 助詞・漢字かながASCII識別子に直接隣接する実運用形式（空白なし）。
        # Unicode \b はこの境界で成立しないため、ASCII構成文字ベースの境界で検出する。
        samples = [
            "PR #899を統合・現行mainへVM反映、Snake復帰カテゴリのlive境界を確認",
            "PR770を統合",
            "PR-770を修正",
            "docich#770の参照更新",
            "5ec6fdd88909aa554e02859170b4d8d711be0db4へデプロイ",
            "tests/test_title_day.pyのCI成功",
            "現行mainへ反映済み",
            "VM上で稼働中",
            "root v763 継続中の攻略",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(looks_internal(sample))

    def test_viewer_wording_is_not_flagged_internal(self):
        samples = [
            "AIの取引コーナーからゲームへ、画面切替を改善",
            "NetHackでAIが長期攻略に挑戦",
            "視聴者参加のAI探偵ミステリーコーナーを追加",
            "半熟英雄でAIが王国を運営",
            "ペーパートレード AIの検証配信",
            "ソ連ゲーム AIプレイ配信",
            "ゲームのmaintainではなく体験を改善する",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(looks_internal(sample))

    # --- 候補メタデータ契約 (docich#789 §1) ---

    def test_deployed_and_verified_candidates_parse(self):
        for status in ("deployed", "verified"):
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as d:
                    path = self.candidate_file(Path(d), candidate_text(status=status))
                    candidate = parse_viewer_candidate(path)
                    self.assertIsNotNone(candidate)
                    self.assertEqual(candidate.status, status)
                    self.assertTrue(candidate.is_publishable_stage)

    def test_implemented_and_merged_parse_but_are_not_publishable(self):
        for status in ("implemented", "merged"):
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as d:
                    path = self.candidate_file(Path(d), candidate_text(status=status))
                    candidate = parse_viewer_candidate(path)
                    self.assertIsNotNone(candidate)
                    self.assertFalse(candidate.is_publishable_stage)
                    self.assertIsNone(adoptable_candidate(path, None))

    def test_expired_candidate_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), candidate_text(generated_at=iso_days_ago(8)))
            self.assertIsNone(adoptable_candidate(path, None))

    def test_future_dated_candidate_is_not_adopted(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), candidate_text(generated_at=future))
            self.assertIsNone(adoptable_candidate(path, None))

    def test_ttl_env_override_expires_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), candidate_text())
            with mock.patch.dict("os.environ", {"STREAM_VIEWER_TITLE_TTL_DAYS": "0"}):
                self.assertIsNone(adoptable_candidate(path, None))

    def test_missing_or_invalid_metadata_is_fail_closed(self):
        cases = [
            "# generated\n- 裸のタイトル行は契約メタデータを持たない\n",
            "viewer_title: タイトルのみ\n",
            "viewer_title: タイトル\nstatus: shipped\n",
            "viewer_title: タイトル\nstatus: deployed\n",
            "viewer_title: タイトル\nstatus: deployed\ngenerated_at: not-a-date\n",
            f"viewer_title: タイトル\nstatus: unknown\ngenerated_at: {now_iso()}\n",
            # 根拠（rationale）は最低限必要な契約情報。空欄は採用しない。
            f"viewer_title: タイトル\nstatus: deployed\ngenerated_at: {now_iso()}\nrationale: \n",
        ]
        for body in cases:
            with self.subTest(body=body):
                with tempfile.TemporaryDirectory() as d:
                    path = self.candidate_file(Path(d), body)
                    # いずれのケースも採用されない（parse まで失敗する場合と、
                    # parse は通るが採用契約で落ちる場合がある）。
                    self.assertIsNone(adoptable_candidate(path, None))

    def test_rationale_is_required_for_adoption(self):
        with tempfile.TemporaryDirectory() as d:
            no_rationale = self.candidate_file(
                Path(d), candidate_text(rationale=None)
            )
            self.assertIsNone(adoptable_candidate(no_rationale, None))
            self.assertEqual(
                choose_body(
                    current="[day190] NetHackで長期攻略に挑戦",
                    candidate_file=no_rationale,
                    fallback=None,
                ),
                "NetHackで長期攻略に挑戦",
            )

    def test_rationale_text_is_never_published(self):
        rationale = "内部向けの根拠テキスト PR #999をmainへマージ"
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(
                Path(d), candidate_text(rationale=rationale)
            )
            title = compose_title(
                day="249",
                activity=None,
                strategy="",
                candidate_file=path,
                fallback=None,
            )
            self.assertNotIn(rationale, title)
            self.assertNotIn("999", title)

    def test_rejected_candidate_text_is_not_readopted_via_current_body(self):
        # 期限切れ候補の本文が現タイトルと同じでも、再採用せず fallback へ移る。
        same_text = "NetHackでAIが長期攻略に挑戦"
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(
                Path(d), candidate_text(title=same_text, generated_at=iso_days_ago(8))
            )
            self.assertEqual(
                choose_body(
                    current=f"[day190] {same_text}",
                    candidate_file=path,
                    fallback=None,
                ),
                DEFAULT_FALLBACK,
            )

    def test_scope_mismatched_candidate_text_is_not_readopted_via_current_body(self):
        # category-only で別カテゴリへ移動した後も、scope不一致の本文を維持しない。
        same_text = "NetHackでAIが長期攻略に挑戦"
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(
                Path(d), candidate_text(title=same_text, valid_scope="NetHack")
            )
            self.assertEqual(
                choose_body(
                    current=f"[day190] {same_text}",
                    candidate_file=path,
                    fallback=None,
                    current_scope="Puyo Puyo",
                ),
                DEFAULT_FALLBACK,
            )

    def test_different_safe_current_body_is_still_preserved(self):
        # 候補が不適格でも、候補本文と異なる安全な現タイトルは維持する（既存挙動）。
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(
                Path(d),
                candidate_text(title="別の候補文面", generated_at=iso_days_ago(8)),
            )
            self.assertEqual(
                choose_body(
                    current="[day190] 現在の一般向けタイトル",
                    candidate_file=path,
                    fallback=None,
                ),
                "現在の一般向けタイトル",
            )

    def test_scope_contract(self):
        scoped = candidate_text(valid_scope="NetHack")
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), scoped)
            self.assertIsNotNone(adoptable_candidate(path, "NetHack"))
            self.assertIsNone(adoptable_candidate(path, "Puyo Puyo"))
            self.assertIsNone(adoptable_candidate(path, None))
        unscoped = candidate_text(valid_scope=None)
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), unscoped)
            self.assertIsNotNone(adoptable_candidate(path, None))
            self.assertIsNotNone(adoptable_candidate(path, "Soviet Game"))

    # --- 選択経路 ---

    def test_explicit_valid_candidate_wins(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), candidate_text())
            self.assertEqual(
                choose_body(
                    current="[day190] 以前の一般向けタイトル",
                    candidate_file=path,
                    fallback=None,
                ),
                "AIの取引コーナーからゲームへ、画面切替を改善",
            )

    def test_safe_current_title_is_preserved_when_candidate_missing(self):
        self.assertEqual(
            choose_body(
                current="[day190] NetHackで長期攻略に挑戦",
                candidate_file="/definitely/missing",
                fallback=None,
            ),
            "NetHackで長期攻略に挑戦",
        )

    def test_internal_current_title_falls_back(self):
        self.assertEqual(
            choose_body(
                current="[day190] PR770をmainへマージ、VM反映と実機状態を確認",
                candidate_file="/definitely/missing",
                fallback=None,
            ),
            DEFAULT_FALLBACK,
        )

    def test_unusable_candidate_does_not_replace_safe_current(self):
        cases = [
            candidate_text(status="implemented"),
            candidate_text(status="merged"),
            candidate_text(generated_at=iso_days_ago(8)),
            candidate_text(valid_scope="NetHack"),
            "# generated\n- 裸のタイトル行は契約メタデータを持たない\n",
        ]
        for body in cases:
            with self.subTest(body=body.splitlines()[1] if len(body.splitlines()) > 1 else body):
                with tempfile.TemporaryDirectory() as d:
                    path = self.candidate_file(Path(d), body)
                    self.assertEqual(
                        choose_body(
                            current="[day190] AIたちがゲームに挑戦中",
                            candidate_file=path,
                            fallback=None,
                        ),
                        "AIたちがゲームに挑戦中",
                    )

    # --- 組成 ---

    def test_compose_uses_valid_candidate_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.candidate_file(Path(d), candidate_text())
            title = compose_title(
                day="249",
                activity=None,
                strategy="決めていきます",
                candidate_file=path,
                fallback=None,
            )
            self.assertEqual(
                title,
                "[day249] AIの取引コーナーからゲームへ、画面切替を改善 決めていきます",
            )

    def test_compose_ignores_unpublishable_candidate_from_file(self):
        for status in ("implemented", "merged"):
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as d:
                    path = self.candidate_file(Path(d), candidate_text(status=status))
                    title = compose_title(
                        day="249",
                        activity=None,
                        strategy="",
                        candidate_file=path,
                        fallback=None,
                    )
                    self.assertEqual(title, f"[day249] {DEFAULT_FALLBACK}")

    def test_compose_rejects_internal_activity_and_strategy(self):
        title = compose_title(
            day="249",
            activity="PR #899を統合・現行mainへVM反映",
            strategy="root v900 継続",
            candidate_file=None,
            fallback=None,
        )
        self.assertEqual(title, f"[day249] {DEFAULT_FALLBACK}")
        self.assertNotIn("899", title)
        self.assertNotIn("v900", title)

    def test_compose_keeps_public_names_and_limit(self):
        title = compose_title(
            day="249",
            activity="NetHackでAIが長期攻略に挑戦 " + "あ" * 200,
            strategy="慎重に探索中",
            candidate_file=None,
            fallback=None,
        )
        self.assertTrue(title.startswith("[day249] NetHack"))
        self.assertIn("慎重に探索中", title)
        self.assertLessEqual(len(title), 140)
        self.assertNotIn("\n", title)


if __name__ == "__main__":
    unittest.main()
