# 0.30.4 の検証範囲

- Windowsで `python -m unittest discover -s tests -v` の448件と `python scripts/check_package.py` が成功しました。ローカル転送は3種類の回答状態と保存イベントの依頼ID・入力ハッシュ、時間切れ時の挙動を確認しました。既存のハードリンク検証を含む全体テストは、Windowsサンドボックスの外で実行しました。
- `node --test tests/test_chat_browser.cjs` でBrowser操作を模擬し、送信前の本文照合、不確定なクリックの再試行防止、終了表示を待つツール呼出し、対応する最終回答だけの転送を確認しました。
- 2026-11-10〜11 JSTの作業中、デスクトップのBrowserがローカル模擬ページへ到達できず、エラーページがナビゲーション制限で停止しました。実UIでの終了表示と長時間待機、通常Chat Proへの送受信、稼働中のホストへの導入は未検証です。次に承認済みの実依頼を扱う際、[Browser手順](../plugins/codex-task-routing/skills/task-routing/references/chat-transfer.md#browser-script-and-completion-signal)で終了表示と保存イベントを確認します。
