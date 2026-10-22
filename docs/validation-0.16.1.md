# 0.16.1 の検証範囲

2026-10-22、専用ブランチ`codex/fix-long-path-injection`で、長いインストール先でSessionStart/SubagentStartの注入が8000字を超え、方針が適用されずsystemMessageになる問題を直した。基点は`f3b9bef32cb3c9a417ef752b89747c3267913044`（origin/main、版0.16.0）。

## 原因と意味変更なし

- 注入文が導入先の絶対パスを繰り返していた。プラグインルートは通常Chat有効時のSessionStartで4回（観測の開始・完了の2コマンド、ローカル認証、Chat参照）、方針キャッシュは4回（有効方針と3つの詳細ファイル）入っていた。長さと回数の積が8000字の上限を超えた。
- 変更: `Plugin root`と`Rendered policy directory`の絶対パスを各1回だけ示し、他の参照は`<plugin-root>/...`の相対パス、またはキャッシュ内のファイル名にした。観測手順の説明も、フックが示すPlugin rootから`scripts/routing.py`を組み立てる形に合わせた。
- 相対参照の根拠: 既存のSKILL.mdが`python <plugin-root>/scripts/routing.py status --json`と「スキルフォルダの2階層上がplugin-root」を規定し、スキル内の参照も相対で書いている。有効方針も詳細文書を`model-routing-policy.md`等の名前で指しており、同じ解決規則に従う。
- 方針本文、既定のモデル・effort、通常Chatの条件、明示overrideは変更していない。`defaults/`を変えていないため、`policy_revision`、provenance、生成fixtureは更新していない。

## 注入字数（既定の字数上限は8000）

同じ一時ホストを使い、導入先だけを変えて測定した。一時ホストのパスは132字で、実利用のCodexホームより長いため、キャッシュ部分は実際より大きく出ている。

| 導入先（プラグインルート） | 既定 SessionStart | 既定 SubagentStart | 通常Chat有効 SessionStart | 通常Chat有効 SubagentStart |
| --- | --- | --- | --- | --- |
| 短い（58字） 変更前 → 変更後 | 5193 → 4449 | 3136 → 2487 | 7514 → 6725 | 5696 → 5002 |
| 177字 変更前 → 変更後 | 5550 → 4568 | 3255 → 2606 | 7990 → 6844 | 5934 → 5121 |
| 201字 変更前 → 変更後 | 5622 → 4592 | 3279 → 2630 | 8086（上限超過で未適用） → 6868 | 5982 → 5145 |

変更後の注入は、導入先の字数に対して1字ずつ増えるだけで、回数による増加はなくなった。

## ローカル検証

Windows 11 / Python 3.13.15で実施。

| 検証 | 結果 |
| --- | --- |
| `python -I -m unittest discover -s tests -v`（短い導入先、変更前） | 264件成功 |
| 同（短い導入先、変更後） | 265件成功（新規1件を含む） |
| 同（177字の導入先、変更前） | 264件中6件失敗。通常Chat有効時の注入上限超過による失敗で、test_chatgpt_hookの5件とtest_local_auth_routingの1件 |
| 同（177字の導入先、変更後） | 265件成功 |
| 同（201字の導入先、変更後） | 264件成功、1件失敗。test_updater.test_timeout_kills_child_after_parent_exit。更新の状態ディレクトリ（作業ディレクトリ、278字）がWindowsのMAX_PATHを超え子プロセスを起動できないため。origin/mainでも同じ失敗で、注入の変更とは別件 |
| 新規の200字級回帰テスト `test_two_hundred_character_install_root_is_stated_once_within_hook_limit` | 変更前のコードでは失敗、変更後は両方のフックと両通信方式で成功。導入先が1回だけ現れ、キャッシュ名も1回だけ現れることを確認 |
| `python -I scripts/check_package.py` | 成功 |
| `RELEASE_BASE=origin/main python -I scripts/check_release.py` | 成功（0.16.0から0.16.1へ） |
| `git diff --check` | 成功 |

## 版と記録

- 0.16.1は、origin/main（0.16.0）の配布修正として付けた。PR #28の0.17.0とは番号が衝突しない。0.16.1を先にマージすればPR #28は0.17.0のまま残り、番号の変更は不要になる。PR #28が先にマージされる場合は、このPRを0.17.1へ上げて再確認する。
- 変更履歴では「配布修正」と明記し、方針の意味変更として扱わなかった。

## 未検証

- 実際のCodex本体・フックの信頼と再配送、通常Chat画面、実セッションでモデルが`<plugin-root>`の相対参照を正しく解決することは未確認。
- Codex CLIが利用できる環境がなく、`scripts/smoke_native.py`は実行していない。変更箇所（導入先の表示と`Plugin root`の検査）は静的に確認した。
- 他OSの結果はPRのCIで別途確認する。
- 201字の導入先で残る`test_timeout_kills_child_after_parent_exit`は、更新処理の作業ディレクトリ長の問題であり、本修正では扱っていない。
