# 0.18.0 の検証範囲

2026-10-22、`codex/native-cloud-continuation`で既存Codex Cloudのnative継続・回収契約を追加。基点は`b9b41e4`（0.17.0、PR #28/#29/#30を含む）。

## 変更と互換性

- 新しい`cloud-native`はschema 2のローカルplanner。確認した既存Codex durable host/taskを固定し、inspect / dispatch-plan / receive / wait / acceptを分ける。汎用app toolの呼出し条件・本人承認・実送信成功・実検証・受入を同一にしない。
- `NativeClaimStore`はschema 3、UIタブを作らずtargetとprevious_request_idを固定する。前依頼の回収、永久claim、task mutex、単一後続枠により、scope変更した新依頼も同じ元taskへ順に継続できる。予約後のcrashは保留のまま残す。
- UIの`cloud` schema 1と`ClaimStore`保存schema 2の入力・出力を維持。未知・破損・旧記録は削除・移行・未送信扱いにせず、再送を止める。新規Cloud作成API、送信dispatcher、Work/旧CLIへの切替を追加しない。
- nativeを許す新方針と現在のホスト許可をrootが確認してから、正式なsend_message_to_threadを一度呼ぶ手順を明文化。現在注入中の旧UI-only指示はこの担当工程では維持し、コードを実装しただけでは実送信しない。
- defaultsのsummary/policy_sessionsとCloud関連template、SKILL/reference/README、fixtures/provenance・配布検査を同期。0.17の裏取り・長文返却・updater fixture修正を維持する。モデル/effort・親設定・通常Chat/認証条件を変更しない。

## ローカル検証

Windows / Python 3.13.15。実一時ファイル・OS lockのテストは既知のsandbox ACL制約を避け、通常権限で行う。

| 検証 | 結果 |
| --- | --- |
| native専用ケース | 19件成功。kind/host/taskとscope固定、方針/能力/承認の不足、wait→receive、今回応答の照合、永久claim・後続単一枠・保存失敗を検証 |
| `python -m unittest discover -s tests -v` | 全286件成功、skipなし、終了コード0（11.842秒）。既存UI/ChatとPR #28/#29/#30の検証を含む |
| `python scripts/check_package.py` | 成功。native reference、既存構成、公開物の個人パス・秘密値・未完成素材を検査 |
| fixtures/provenanceと既定値 | 3生成文書のhash一致。models全体、summary/policy_sessions以外のprinciplesは基点と同一 |
| 通常Chat有効時hook | 一時CodexホームでSessionStart 6740字 / SubagentStart 4988字、上限8000字以内 |
| 版検査 | 作業差分のSemVer判定で0.17.0→0.18.0が有効。`check_release.py --base b9b41e4`終了0。未commit段階ではbase..HEADに今回差分がないため、commit後にも親担当が再検査する |
| `git diff --check` | 成功 |

初回の専用テストは、保存失敗を模擬するテスト自身のmockが再帰した。mock前に元関数を保持して修正し、19件成功を確認した。製品の安全検査を弱めていない。

## 実Cloudの境界

| 操作 | 実証と未検証 |
| --- | --- |
| 既存Cloudのread_thread | 親担当が、既存1件についてkind=codex / hostId=durable / 元taskの完了結果を取得済み |
| send_message_to_threadによる継続 | 汎用schemaは存在するが、この既存Cloudへの実継続は未検証。この担当は送信しない |
| wait_threads | 親担当の実呼出しは汎用エラーで成功未確認。read成功からwait成功を推測しない |
| 今回の依頼の実テスト・受入 | 未実施。ローカル試験の成功を実Cloud成功にしない |
| 新規Codex Cloud作成・移管 | 現native対象schemaに作成先がなく、handoffのCloud移管も非対応。Work Cloud作成は別経路であり代用しない |

新方針を許すホストで、元taskをreadして有効方針・host許可・最新scopeを確認し、新規claimに対応する一度の正式継続、今回応答の回収、実検証と意味確認まで行った時に、その範囲を追記する。メタデータの自己申告やschema上のツール可用性を実成功としない。

## 後処理と引継ぎ

テストfixtureと一時Codexホームは後処理済み。新しいBrowserタブ、Cloudタスク、外部送信、worktreeはこの担当工程で作成していない。既存の利用環境・ユーザー設定・保存済みCloud記録は変更していない。専用ブランチを保持し、親の受入後にcommit/push・PR・CI、commit後のrelease検査を行う。
