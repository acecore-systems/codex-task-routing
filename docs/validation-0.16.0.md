# 0.16.0 の検証範囲

2026-10-21、専用ブランチ`codex/session-routing-decisions`で、目的単位からのセッション推薦を追加した。基点は`810cdf89161982c9f39d8e7e99de64a6509d9328`。

## 意味変更と互換性

- 新しい`session_plan.py recommend --facts`は、候補を事前指定せず仕事・継続担当・実行適合・host条件を比較する。同じ完了単位の進行中担当と、親にも仕事を実行できるという能力を区別する。
- 新規の独立repo実装・テスト・長時間完了単位ではCloudを有力候補にする。密結合や引継ぎ便益不足なら継続、同一目的の限定補助は標準子、独立継続管理やローカル依存は別セッションを比較する。
- 材料と実際のChat能力で完結する工程は既存Pro優先を維持する。Cloudを常にProより優先せず、モデル/effort・override・親設定を変更しない。
- 三値のunknown、host拒否、材料転送未確認、Cloud利用不能、必須テスト不足を不足または代案の理由として保持する。推薦の成功で作成・送信を承認せず、実行は既存のhost契約で確認する。
- 既存`session`と`cloud`のschema 1・返却値、Cloud UI専用経路、最小claimのschema 2と排他・元task固定・巻戻し/再送拒否は変更しない。送信済み・結果不明・status要求・引継ぎ・受入待ちを新規送信理由にしない。
- defaultsのsummary/policy_sessions、生成fixture・provenance、SKILL、catalog、README、入力例・短い提案例・境界ケースを同期する。通常会話での提案も可能で、毎回のCLI入力を義務にしない。

## ローカル検証

Windows / Python 3.13.15で実施。初回の制限付き実行は、既存の一時保存先・hardlink ACL制約で保存テストが失敗した。通常権限で全体を再実行し、コードや保存安全性を弱めず解消を確認した。

| 検証 | 結果 |
| --- | --- |
| 新しい推薦のケーステスト | 17件成功。Cloud、継続担当、密結合、標準子、ローカル依存、Pro適合、未確認・拒否、CLI入力契約・非実行を検証 |
| `python -m unittest discover -s tests -v` | 全264件成功、skipなし、終了コード0（14.170秒） |
| `python scripts/check_package.py` | 成功。manifest・参照・defaults・公開物の個人パス/秘密値/未完成素材を検査 |
| 一時Codexホームのrender | 生成fixtureとprovenanceを同期。稼働利用環境は変更していない |
| 元のplanner契約 | 新recommendとCLI分岐以外の既存関数のASTが基点と同一。session/cloudの既存テストも成功 |
| 既定値と生成hash | models全体・summary/policy_sessions以外のprinciplesは基点と同一。rendered_sha256と生成文書が一致 |
| 通常Chat有効時のhook | SessionStart 7458字 / SubagentStart 5624字で既存8000字上限内 |
| 版と差分 | 作業差分のSemVer判定で0.15.2→0.16.0が有効、`git diff --check`成功。commit後のreleaseコマンドは親担当で確認 |

テストは供給された架空の条件からの判断と既存のローカル機能を検証する。入力のtrue、helper成功、ローカル試験成功は、実接続・送信承認・Cloudでのテスト実行の証拠ではない。

## 実Cloudと後処理

この担当工程では0.16.0の実Cloud送信・回収・Linuxテストは未実施。過去の版の実Cloud実績は[版別記録](validation.md)と分ける。親担当による今回差分のCloudレビュー・実Linuxテストは別工程で、その結果を確認した版・環境・対象差分に紐づけて追記する。

試験用保存先と一時Codexホームはfixtureが後処理した。新しいBrowserタブ・Cloudタスク・worktreeをこの担当工程では作成していない。無関係のworktree・既存保存記録を変更せず、受入・commit/push・PRは親担当へ引き継ぐ。
