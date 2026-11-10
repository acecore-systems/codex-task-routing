# 0.30.4 の検証範囲

- Windowsで `python -m unittest discover -s tests -v` の457件と `python scripts/check_package.py` が成功しました。ローカル転送は3種類の回答状態と保存イベントの依頼ID・入力ハッシュ、時間切れ時の挙動を確認しました。既存のハードリンク検証を含む全体テストはWindowsサンドボックスの外で実行しました。
- `node --test tests/test_chat_browser.cjs` の4件でBrowser操作を模擬し、送信前の本文照合、本文入力中にGitHubの選択が失われた場合の停止、不確定なクリックの再試行防止、終了表示の待機、対応する最終回答の転送を確認しました。架空のPRでのCLI確認では、モード未観測、パーソナライズなし、操作の提供のみ、マージの承認なしがそれぞれ送信前に止まりました。
- 2026-11-11 JSTにWindowsの組み込みBrowserで新規一時チャットを開きました。「パーソナライズなし」から「パーソナライズ」に切り替え、Proが選ばれた状態でGitHubを検索・選択し、入力欄にGitHubが表示されることを確認しました。空の確認用チャットは送信せず閉じました。対象リポジトリの操作権限とマージ実行は未検証です。
- デスクトップのBrowserからローカル模擬ページへの接続は時間切れになりました。実UIでの終了表示と長時間待機、通常Chat Proへの送受信、稼働中のホストへの導入は未検証です。次に承認済みの実依頼を扱う際、[Browser手順](../plugins/codex-task-routing/skills/task-routing/references/chat-transfer.md#browser-script-and-completion-signal)で終了表示と保存イベントを確認します。
