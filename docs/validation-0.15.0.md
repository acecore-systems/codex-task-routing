# 0.15.0 の検証範囲とクラウド実行への引継ぎ

確認日: 2026-10-20。

## この変更の範囲

別セッションの提案とクラウドタスクの引継ぎ・段階別判定を追加する。
担当モデル・固定effortの変更ではなく、現在の担当を継続するか、独立した仕事として分けるかの判断を追加する方針変更である。
ヘルパーは入力された観測を検査する。実際の認証、ホストの許可、環境の実在、モデルの実行、テスト成功を証明しない。

この版は送信を実行するruntimeを持たない。フックからネットワーク、モデル、別CLIセッションを起動しない。
通常Chatのopt-inをクラウドの実行承認へ転用しない。既存の有効な承認は対象と操作の範囲内で再利用し、実際のホスト制約を維持する。
稼働中の利用環境へのインストール、ユーザーの設定変更、クラウドタスクの投入、マージは実施していない。

## ローカル検証

Windows / Python 3.13.15で、最終差分に対して以下を実行した。

| 検証 | 結果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 230件成功、skipなし、終了コード0 |
| `python scripts/check_package.py` | 成功、終了コード0 |
| `git diff --check` | 成功、終了コード0 |

新規31件の回帰テストは、候補提案と実行許可の分離、既存担当の再利用、未確認状態、送信結果不明時の再送拒否、操作別確認、元task/repo/revision照合、変更証拠、必須検証、所有者受入、既知の失敗とレビュー待ちの区別を扱う。
環境・対象revision・検証手段は許可確認だけでは準備完了にせず、実観測を要求する。

初回の全体検証では、追加要約によって長い導入パスでのhook文字数上限テスト2件が失敗した。
8000文字の上限を維持して追加要約を短縮し、最終実行で解消を確認した。
詳細文書のfixtureは一時Codexホームを使って再生成し、provenanceのhashを更新した。
テスト用ディレクトリはfixtureで清掃し、稼働中のCodexホームは変更していない。

GitHub CIでも実装commit `a6b42b8aef87ab67f9f8d047f19384238152e585`に対し、
Windows/Python 3.11・3.13、Ubuntu/Python 3.11、macOS/Python 3.11の4構成が成功した。
[Checks #70](https://github.com/acecore-systems/codex-task-routing/actions/runs/39410826998)。
これは各OSでのテスト・配布・版更新検証であり、実クラウドへの投入やアプリ連携の実証ではない。

## 公式資料と実際の入口

- [Codex Cloud](https://learn.chatgpt.com/docs/cloud): クラウドのタスクと環境の概説。
- [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments): 公開済み環境からタスクごとの作業領域を開始し、既存タスクは自身の保存状態を継続する。保存設定と環境の公開を区別する。
- [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli): `codex cloud exec`、環境指定、クラウドタスクの一覧取得。

旧 `cloud-environment`（単数）の資料はLegacyである。新旧の環境やCLIとの互換性は、資料の名称だけでは確定しない。

Windowsで `codex-cli 0.163.0-alpha.10.2` のヘルプを確認した。

| コマンド | 確認できたインターフェース | 確認していないこと |
| --- | --- | --- |
| `codex cloud exec` | `--env`必須、`--branch`指定、`--attempts`既定1 | 実投入、環境との互換性、cloudでのmodel/effort反映 |
| `codex cloud status` | 対象task IDによる状態確認 | 実タスクの状態取得 |
| `codex cloud list` | `--env`、`--limit`（1〜20）、`--cursor`、`--json` | 実一覧の取得、環境一覧の取得 |
| `codex cloud diff` | 対象task IDと任意のattempt指定 | 差分の実回収、テスト証拠 |
| `codex cloud apply` | 対象task IDと任意のattempt指定 | ローカル適用、競合解決、受入 |

ヘルプには一時ディレクトリ作成のアクセス警告が出たが、上記のヘルプ本文と終了コード0は得られた。これは認証失敗やクラウド接続失敗を確認したものではない。

このホストのアプリツールも区別した。

- `create_thread`の`chatgptWorkCloud`は、Codex Cloudの指定環境へ投入する専用経路と同一とは確認できない。
- `handoff_thread`はクラウド移動に非対応。
- `finalize_environment`はsimulated/prototype用で、実クラウド環境の作成証拠にはできない。
- 新規トップレベルタスクの作成・他タスクへの送信には、それぞれ現在のツールが要求する本人の指示が必要。サブエージェントの選定規則で代替しない。

## 実クラウドで残る確認

1. 実行ホストが許すCodex Cloud専用の入口、対象環境ID、repoへの接続と実行権限を確認する。CLIの存在や申告値だけでは実行可能としない。
2. 対象branchと期待revisionを固定し、クラウドが取得する材料・実行するテスト・返却先・完了条件を引き継ぐ。ローカル未commit変更やローカル専用MCPの自動引継ぎを前提にしない。
3. 元依頼で承認された範囲内の小さな1件を、利用可能な正式な経路で一度だけ投入する。送信結果が不明な場合は照合へ戻し、再投入しない。
4. 同じtask IDの結果を回収し、入力の同一性、実際のrevision、差分、実行環境とテスト結果、未検証範囲を確認する。
5. 内容を受け入れてから、許可された適用先へ反映する。タスク終了・差分取得・ローカル適用・PR作成・マージ・本番反映をそれぞれ区別する。

クラウド環境、Browser経由の実投入、Linux/macOSの実アプリ連携、再開をまたぐ実タスクの回収は未検証。
ローカルテストやGitHub CIの成功は、これらの実証に置き換えない。
