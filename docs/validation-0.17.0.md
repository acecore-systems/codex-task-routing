# 0.17.0 の検証範囲

2026-10-22、専用ブランチ`codex/adopt-claude-routing-rules`で、委譲結果の裏取り・ユーザー確認前の選択肢出し切り・長文成果物の返却形式を追加した。基点は`f3b9bef32cb3c9a417ef752b89747c3267913044`。

## 意味変更と互換性

- 子の報告に含まれるファイル名・関数名・API・数値・コマンドなど確かめられる事実は、親が裏を取ってから採用する。重要な判断に直結するものは親が自分で確認する（policy_handoff）。
- ユーザーへ技術的な選択肢を示す・質問する前に、担当に他の方法を聞いて選択肢を出し切る。ユーザーに聞くのは出し切っても残る判断・要件確認だけとする（policy_escalation）。
- 一般知識のWeb検索は、親が繰り返し検索する前に利用できる検索手段を確認してLunaで済むか検討する（分類表の1行）。一次情報の裏取りが要る時だけ親が行う。
- 長い成果物（下書き・調査結果）は子に指定ファイルへ書かせ、親はパスと要点だけを受け取る（引継ぎテンプレートの共通節）。
- 要約（`principles.summary`）は変更していない。したがってSessionStart・SubagentStartに注入される本文は変わらない。追加は詳細節とテンプレートへ限定した。
- Codex固有の制約（外部モデルを起動しない、外部委譲は原則同時1件、子のeffortは固定値）は変更しない。親設定・モデル/effort・既存overrideと通常Chat条件も維持する。
- `policy_revision`を`2026-10-22-handoff-verification`へ、manifestを0.17.0へ更新した。生成fixtureと`rendered_sha256`を同期した。

## 注入字数

文字数は Python の `len`。ワーキングツリーの場所は`C:\Users\gnish\repos\ctr-claude`（31字）。

| 注入 | 変更前 | 変更後 | 上限 |
| --- | ---: | ---: | ---: |
| 既定 SessionStart | 5,053 | 5,053 | 8,000 |
| 既定 SubagentStart | 2,996 | 2,996 | 8,000 |
| 通常Chat有効 SessionStart | 7,374 | 7,374 | 8,000 |
| 通常Chat有効 SubagentStart | 5,556 | 5,556 | 8,000 |

## ローカル検証

Windows 11 Pro / Python 3.13.15、基点の新規clone（短いパス）で実施。

| 検証 | 結果 |
| --- | --- |
| 変更前のベースライン `python -I -m unittest discover -s tests -v` | 全264件成功 |
| 変更後の `python -I -m unittest discover -s tests -v` | 全264件成功、終了コード0（14.190秒） |
| `python -I scripts/check_package.py` | 成功 |
| `RELEASE_BASE=origin/main python -I scripts/check_release.py` | 成功（0.16.0→0.17.0が有効） |
| `git diff --check` | 成功 |
| 注入字数の測定 | 上表のとおり。変更前後で同一 |
| 生成fixtureと`rendered_sha256` | 3文書の`rendered_sha256`がtests/fixturesのSHA-256と一致 |

## 未検証

- Linux・macOSの結果はPRのCIで別途確認する。
- 実際のCodexホストでのフック信頼、新しいタスクでの新方針の読込み、子の実際の挙動は未確認。
- `scripts/smoke_native.py`（隔離Codexホームへの導入・両フック・再導入・解除）は今回未実行。
- 通常Chat、Cloud、ローカル認証の実接続は今回の範囲外。
- 作業場所が長いパス（ルート約148字）の場合、通常Chat有効時のSessionStart注入が8,000字を超え、方針が適用されず既存テスト6件が失敗することを別途確認した。同じ版を短いパスで実行すると全件成功する。注入はインストール先の絶対パスを含むため、利用者の導入先が長いパスの場合の注入余裕は要確認。
