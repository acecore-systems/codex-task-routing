# 0.12.0の検証と利用環境への引継ぎ

基準はPR #17マージ後のmain `3238b4504a65264e54dbe15772a152b55aa5bdc8`、基準ツリーは`9daeb80dde96f6ec509e02262dd9d17596e378a8`です。専用の`codex/browser-operation-preflight`で作業しました。

## 修正した機械判定

- 0.11.0の、確認済みの転送許可に対する二重の必須証拠入力を撤回。旧trueのみを有効な確認として再利用でき、未確認・旧falseを自動でtrueにしません。
- 新規のbrowser_checksはinspect/send/receiveの操作観測を入力する代替形式で、旧booleanと証拠ファイルの両方を要求しません。初回の許可済み実作業に試験書込みや実行成功済みを要求しません。
- inspectは送信許可・材料・モデルが未確認でも調査自身の条件で判定。実送信は既存のroot/opt-in/材料/操作承認/モデル/利用枠/期限/host条件を維持。
- receiveは妥当な既存request/bundle、回収許可・対象・新しいUI観測を確認。新規生成の枠切れやsendだけの拒否から、許可済みの回収まで止めません。再送は案内しません。
- 機械的な不足条件に、担当・確認場所・対象操作・具体的な解消手順を付与。provider_reviewは具体的な条項適用の未解決点を表し、全ユーザーへ新しい提供者承認を要求するゲートではありません。

## 実行済み検証

隔離Linux / Python 3.13.5で、次を実行しました。

```text
python -m unittest discover -s tests -v
# Ran 183 tests; OK (skipped=2)
python scripts/check_package.py
# Package checks passed
```

23テストを追加し、旧必須証拠テストを重複不要の回帰テストへ変更しました。Linuxの2件skipは既存のWindows固有ケースです。検証は架空のホスト観測を入力し、未確認・不許可を昇格しないこと、既存の許可済み経路が追加承認なしで送信候補になること、回収限定の許可が新規送信を許可しないこと、ローカル回答のID/hash・保存・同一再試行を対象にしています。**実Browserや実モデルをモックの成功と取り違えません。**

最終HEADのPR CIとマージ状態は[PR #18](https://github.com/acecore-systems/codex-task-routing/pull/18)の対象SHAとChecksで確認します。既存のWindows 3.11/3.13、Ubuntu 3.11、macOS 3.11マトリクス・release checkを変更していません。ローカルGit基準は上記mainとツリーが一致するスナップショットです。直接cloneはDNS解決に失敗したため、一時CI成果物で追跡済みソースを取得しました。補助ファイルは最終配布差分へ含めません。

## 実ホストと公開仕様の確認範囲

本作業の通常Chatでは、Plugin ManagementでBrowserの権限を問い合わせると`not_installed`でした。これは**本セッションの状態**であって、利用者のデスクトップCodexのインストール・操作仕様・サイト許可を観測した結果ではありません。利用者側の実BrowserからChat Proへ依頼を送る操作、回答を回収する操作はどちらも未実施です。通常ChatのGitHub操作成功はこの二つの代替証拠にしません。

公開公式Browser資料ではページ操作・サイト許可の再利用・アクション確認・別プロファイル等を確認しました。利用者が申告したBrowserの具体的な操作スキーマは、本Chatから取得できていません。一般利用規約の自動Output抽出条項と、今回の公式Browserによる単発の表示回答回収との関係を明示した公式説明も、取得資料では確認できませんでした。残る条件の担当・場所・限定したサポート照会文は[許可手順](../plugins/codex-task-routing/skills/task-routing/references/chat-authorization.md)に記載しています。

## 利用環境への反映

稼働中の利用者環境へのインストール、親設定・認証・個人のAGENTS.md・config.toml編集はしていません。既定モデル/effortのmodelsオブジェクトは基準mainから変更せず、Sol子はgpt-6.1-sol/maxを維持します。ネイティブCodex CLIは検証コンテナにないため、その実導入・解除スモークも未実行です。

既存のmain追従登録は、対象プラグインを利用するセッションを終了後、本人のローカル端末で次を実行します。固定SHA登録は自動でmainへ切り替えず、READMEの固定版運用を維持します。

```text
codex plugin marketplace upgrade codex-task-routing
```

再開した対象Codexセッションで、導入済みmanifestが0.12.0、`routing.py status --json`のSolがgpt-6.1-sol/max、`chatgpt_route.py status`で既存opt-in/transport/旧Pro表示設定が維持されたことを確認します。状態確認は更新の実行成功やBrowser実往復の証明ではありません。

その後、親は既存の本人承認を参照し、許可手順のinspectで実Browserの操作仕様とUIを確認します。条件を満たした操作だけpermission_checkedにし、三操作がそろえば本来の依頼を一度送り、既存bundleを使ってreceive・validate・内容受入まで進めます。満たせない条件は具体的な操作と解消先を報告し、値だけを書き換えて通過させません。
