# 0.13.0の検証と利用環境への引継ぎ

2026-10-14、main `312bdec40c912fb1cde0b54840100a9d722e46f6`を基準に専用branch `codex/specialist-ownership`で作業しました。稼働中のプラグインへの更新・親設定の変更は行っていません。

## 意味を変えた範囲

方針原本の13項目を、親中心から担当による完了単位へ変更しました。利用者がAstra親から開始する運用を想定し、親は判断・優先順位・担当選定・承認境界・受入・重要例外へ集中します。有効な通常Chat Proを新規の適合工程の最初の主担当候補にし、Codex側の現物と反復するまとまった仕事はSol 6.1/maxが調査・通常修正・テスト・明示承認済みPR作成・合意した対象の指摘対応まで継続します。

変更したのは`summary`、`policy_intro`、`policy_effort`、`policy_classification`、`policy_test_image`、`policy_originals`、`policy_retrieval`、`policy_escalation`、`policy_handoff`、`policy_reasoning`、`policy_parent`、`policy_hierarchy`、`policy_economy`です。schemaとmodels全体は基準から一致し、Solは`gpt-6.1-sol/max`、Lunaは`gpt-6-luna/max`、Astra子は`gpt-6-astra/high`を維持します。互換用のterra、他モデル親、既存overrideを削除・自動更新していません。

通常修正・テスト失敗・範囲内指摘は担当内で扱い、最新revision・対象差分・到達点・残条件を維持します。合意済み設計の変更、範囲拡大、新たな権限、重大な影響、未解消の矛盾、検証不能は親へ戻します。未到着レビューを対応済みにせず、PR作成の承認からマージ・公開・本番反映を推定しません。並列の独立親作業、直列のホスト許可、本人同意・MFA・生体認証等の既存条件を維持します。

## ローカル検証

Windows / Python 3.13.15 / Codex CLI 0.162.0-alpha.17.2で実行しました。

```text
python -m unittest discover -s tests -v
# Ran 186 tests; OK (skipなし)
python scripts/check_package.py
# Package checks passed
python scripts/smoke_native.py --output-dir outputs/native-smoke-0.13.0-counted
# 6 checks passed; ok=true
```

3件の契約回帰を追加し、旧親基準が生成文書に残らないこと、担当継続とPR終端が子フックへ渡ること、長いnative導入パスで通常Chat有効時にも開始フックが既存上限内に収まることを検証しました。生成fixturesとprovenanceを原本から同期しています。これらは方針・生成器・フックの契約検証で、実モデルが指示どおり動くことの証明ではありません。

隔離Codexホームのnativeスモークでは、導入、秘密情報なしの認証分類とreference、通常Chatの既定無効、両フック、再導入とoverride保持、解除後の無関係な設定・fixture guidance保持を確認しました。初回は通常Chat有効の開始文脈が8077字となり8000字上限で未適用でした。上限を増やさず、Chat補足の重複を圧縮しました。修正後の実導入パスでSessionStartは7647字、SubagentStartは2865字です。スモーク結果に文字数だけを保存し、会話本文は記録していません。

`git diff --check`も成功しています。PRのrelease検査は基準mainに対して別途実行し、CIは対象SHAのChecksで確認します。CI構成はWindows 3.11/3.13、Ubuntu 3.11、macOS 3.11の既存マトリクスを維持しています。

## 通常Chatの実往復

親側のWindows / in-app Browserで、導入済み0.12.0の通常Temporary Chatを使い、Pro表示を送信前後で確認しました。承認済み設計依頼を一度送信し、約10分後のcompleted設計JSONを依頼IDとinput hashで照合、ローカル保存と`chatgpt_route.py validate`成功まで確認しました。これは今回の設計工程の限定的な実往復で、0.13.0の実運用分担やProによるGitHub実装・テスト・PR作成の受入ではありません。backendのモデルIDは確認できていません。0.13.0ではこの送受信protocolとhelperを変更していないため、その範囲の証拠を再利用します。

長文貼付がテキスト添付へ変わった際は、表示された「テキスト欄に表示」で展開し本文を照合しました。URL chipによる表示用改行は本文と区別し、観測したtext nodeとBRの直列化で一致しました。隠しAPIや直接のDOM書換えは使っていません。これを[転送手順](../plugins/codex-task-routing/skills/task-routing/references/chat-transfer.md)へ反映しました。

初回のフォーム保存はreply・saved event・exit 0・validate成功を確認しましたが、直後のBrowser表示が接続エラーになりました。同じbundle/replyによるローカル保存再確認では成功画面・同一replyのhash・saved event・exit 0を確認でき、表示エラーは再現しませんでした。原因は未確定であり、helperの終了処理やHTTP実装を変更していません。表示エラー時は既存replyとeventを確認し、モデルの依頼を再送しない手順を追記しました。追加のPro呼出しはしていません。

## ローカル認証と未検証範囲

親側で取得したMCP状態はprotocol `0.3.0`、実装`0.5.1`、ready=true、realCredentialsEnabled=false、windowsControlVerified=false、実ログイン未検証です。この状態を有効化していません。ローカル経路は登録済みの秘密値を扱う認証工程に限定し、readyや受付を成功としません。全工程未実装と個別の実装済み段階を分け、検証済み段階が必要条件を満たした後続だけを元担当で再開します。実秘密を使うログイン・API受入は未実施です。

標準子の実model/effort、ホストのフック信頼とlive event、同じ担当を再開できる期間・機能、Proの接続先書込み、実認証、品質・手戻り・利用負担の削減率は今回の機械テストで保証していません。モデルexecutor、認証worker、一般業務用のローカルモデル経路を追加していません。設定設計と監査は通常作業3件の観測比較へ算入しません。

開発repoのAGENTS.mdにある旧親基準の同期は、明示承認待ちのため保留しています。配布の方針原本・テンプレート・SKILL・フックは更新済みです。稼働環境のAGENTS.md・config.toml・認証・フック信頼は編集していません。

## 後処理と利用環境への反映

nativeスモークのfixtureプラグインは解除し、検証用の一時ディレクトリは対象範囲内で後処理しました。必要な検証結果は無視対象outputsに保持します。本担当はBrowserタブを新規作成していません。親側のBrowserタブ閉鎖は親の作業記録で確認します。PRが未完了のため専用branch/worktreeを保持し、他タスクのworktreeは触りません。

利用環境への導入とマージはこのDraft PRの完了とは分けます。既存のmain追従更新は、対象プラグインを使うセッションの終了後に行い、既存の設定・override・フック信頼の条件を維持します。次の実運用では0.13.0の方針受渡しと同じ担当での継続を確認し、機械テストの成功を実モデルの受入へ一般化しません。
