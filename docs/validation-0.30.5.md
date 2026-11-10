# 0.30.5 の検証範囲

- Windowsで `python -m unittest discover -s tests -v` の457件、`python scripts/check_package.py`、`node --test tests/test_chat_browser.cjs` の5件が成功しました。Pythonの全体テストはハードリンク検証を含むためWindowsサンドボックス外で実行しました。
- Browser操作の模擬テストで、新規一時ChatのURL、通常Chat・別origin・重複パラメータの拒否、入力前・入力後・GitHub選択確認中の遷移による停止、送信後の待機・回収を確認しました。
- URL形式は既に観測された新規一時Chatを基準にしています。この版で実Browserへの送信や実際のChat応答は検証していません。URL確認はUI上の一時Chat・モデル・権限の確認に代わりません。
