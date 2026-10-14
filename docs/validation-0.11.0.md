> これは0.11.0時点の履歴です。現行の許可確認・更新手順は[0.12.0の記録](validation-0.12.0.md)を参照してください。

# 0.11.0 検証記録（2026-10-14）

## 基準と変更

PR #16マージ後のmain `790dc717f47c9de94399e7a9fcfcbf80d44960df`を基準に、`codex/chat-pro-authorization`で作業しました。Pro/旧表示の共通判定、別Chat転送の根拠形式と診断、通常Chat内のネイティブ操作との適用範囲を整合しています。根拠のないtrueを移行しないため配布版は0.11.0です。子のmodel/effort、親設定、認証・フック信頼・更新方式は変更していません。

## ローカルで実行した検証

通常Chatの隔離Linuxコンテナ / Python 3.13.5で次を実行しました。

```text
python -m unittest discover -s tests -v
python scripts/check_package.py
```

基準版は147テスト、新版は160テストが成功しています。両版ともWindows固有のパス同一性・renameエラーマッピングの2件はLinuxでskipです。13件の新規テストは表示別名、旧設定の非書換え、設定とUI観測の区別、根拠なし・誤った対象・片方向の証拠による停止、falseの非昇格、上位ゲート維持、入力非変更・安全なエラー、Sol/max維持を扱います。配布チェックはmanifest・参照・hooks・defaults・公開物検査を含みます。

テストは一時Codexホームを使用し、稼働中の利用環境は操作していません。ネイティブCodex CLIがこのコンテナにはないため、`scripts/smoke_native.py`による実CLI導入・解除は未実行です。既存の単体テストに含まれるモック・一時ホームでの検証を、利用者PCの実導入成功とは扱いません。

## 接続と実行環境

この通常ChatのGitHub接続ではrepo/原文/PRの読取り、専用ブランチ作成、ファイル作成、Draft PR #17作成、Actionsの結果・成果物取得が実行成功しました。操作スキーマにあるという事実と実行成功を区別しています。最終変更のコミット・レビュー・CI・マージ結果は、対象HEADと実行履歴が確定したPR #17を正本とします。

コンテナの直接git cloneはDNS解決に失敗しました。基準mainの追跡済みソースだけを専用ブランチの一時CI成果物として取得し、archiveの基準SHAを確認して検証しました。認証ファイル、会話履歴、個人メモリは取得していません。補助ワークフロー・差分搬送ファイルは最終配布差分に含めません。実行ログは配布パッケージに含めません。

## 公式情報と未検証範囲

[許可根拠](../plugins/codex-task-routing/skills/task-routing/references/chat-authorization.md)に公式文書と各条件の適用範囲を記載しています。公式リリースノートのPro選択肢と利用者の現在表示の報告を確認しましたが、利用者端末の実画面は直接観測できていません。実行backend IDも確認していません。

別Chatへの実自動送信・回答回収を許可する、利用者の具体的なホスト契約は未確認です。本版の形式テストをその許可の証明にせず、ネイティブGitHub書込みの成功から転送許可を導きません。`transport_authorized:false`はそのまま停止します。利用枠の実残量、品質・使用量比較、利用者PCの0.10.1からの更新・再起動後のフック配送も未検証です。

## 利用環境への引継ぎ

Codexアプリ・CLI（更新対象を使うChatGPTセッションも含む）を終了し、既存のmain追従の登録で次を実行します。親・認証・override・`chatgpt.json`を書き換える操作は不要です。

```text
codex plugin marketplace upgrade codex-task-routing
```

取得元が固定SHAの場合は自動でmainへ切り替えません。[READMEの同じ版の再現手順](../README.md#更新同じ版の再現解除)で対象版を明示してください。新しいセッションで、導入先の`routing.py status --json`がmanifest_version 0.11.0とSol `gpt-6.1-sol`/`max`（明示overrideがなければ）を返すこと、`chatgpt_route.py status`が旧設定でもPro要件を解決することを確認します。`model_selection_observed:false`と`backend_model:unknown`は正常で、実UI/実行モデルを確認したという意味ではありません。

別Chat転送を使う時だけ、実際の方法と許可根拠を確認し、liveに対象が一致する`transport_evidence`を記録します。提供された一般的なBrowser機能・Pro契約・opt-inを根拠に置き換えません。元の依頼が承認している工程への重複承認は不要ですが、ホスト自身の確認要求は守ります。
