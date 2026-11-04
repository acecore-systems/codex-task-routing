# 0.28.1 の検証記録

Windows / Python 3.13 で `python -m unittest discover -s tests -v` は425件成功、`python scripts/check_package.py` は成功しました。合成入力・応答でmetadata-onlyの本文非保存、キー直文字列の拒否、既存claimと再送拒否、従来の既定動作を確認しました。

実 API・実 Claude Code・実ファイル編集・本番導入は未検証です。キー直文字列の検査は変形・分割された値やモデルが直接書いたファイルの漏出を防ぐ保証ではありません。依頼の承認宣言とホストの実行許可を区別し、既存の承認・repository・入力・予算・claimの検査を維持します。
