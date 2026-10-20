# 0.15.0 の検証範囲とクラウド実行への引継ぎ

確認日: 2026-10-20、追確認: 2026-10-21（日本時間）。

## この変更の範囲

別セッションの提案とクラウドタスクの引継ぎ・段階別判定を追加する。
担当モデル・固定effortの変更ではなく、現在の担当を継続するか、独立した仕事として分けるかの判断を追加する方針変更である。
ヘルパーは入力された観測を検査する。実際の認証、ホストの許可、環境の実在、モデルの実行、テスト成功を証明しない。

計画ヘルパーとは別に、許可されたホストから明示的に使うCLI runtimeを追加する。フックからネットワーク、モデル、別CLIセッションを起動しない。
通常Chatのopt-inをクラウドの実行承認へ転用しない。既存の有効な承認は対象と操作の範囲内で再利用し、実際のホスト制約を維持する。
稼働中の利用環境へのインストール、ユーザーの設定変更、クラウドタスクの投入、マージは実施していない。

## 計画機能の初回検証（2026-10-20）

Windows / Python 3.13.15で、初回の計画機能の差分に対して以下を実行した。

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

## 明示CLI実行機能の追加検証（2026-10-21）

`cloud_run.py`と`cloud_record.py`を追加した差分について、Windows / Python 3.13.15で以下を確認した。

| 検証 | 結果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 251件成功、skipなし、終了コード0 |
| `python scripts/check_package.py` | 成功、終了コード0 |
| `python scripts/check_release.py --base e550e90151bc6dc8680a4ed1d56da495019d0ee1` | 成功、終了コード0 |
| `git diff --check` | 成功、終了コード0 |

追加21件はfixture transportを使い、同時実行時の一度限りの送信、timeout、不完全なclaim、
receipt保存失敗、原依頼・cwd・revision照合、元taskの読取り回収、stdin/argv、
送信先override拒否、壊れた記録の非公開診断を確認した。
実Pythonプロセスの強制終了後にもclaimが残り、OS lockが解放され、再送しないことを確認した。
これらはnative Codex Cloudの認証や実送信・回収を実証するテストではない。

初回の全体検証では、短い有効要約の導線を`cloud_run.py`へ変更したことに対し、
すべての文書に`session_plan.py`を要求する旧アサーション1件が失敗した。
要約は明示実行の導線、詳細文書は計画helperの導線を検証する形へ修正し、最終251件の成功を確認した。
8000文字のhook上限、モデル・effort、通常Chatの有効化条件は維持した。
一時Codexホームでfixture/provenanceを同期し、テスト用保存先は清掃した。

CLI adapterは公式sourceの形式を実装するが、profileの指定だけでinstalled binary互換性や実権限を証明しない。
差分bytesの保存と内容受入を分け、適用・commit・push・マージ・本番反映をrunnerから自動実行しない。
詳細は[明示CLI transport](../plugins/codex-task-routing/skills/task-routing/references/cloud-transport.md)を参照。

追加実装の初回CI（commit `fb352c0`、[Checks #72](https://github.com/acecore-systems/codex-task-routing/actions/runs/39458937363)）は
Windows/Python 3.11・3.13、Ubuntu/Python 3.11で成功したが、macOS/Python 3.11で失敗した。
新しいテストfixtureが一時ディレクトリのシンボリックリンクを含むパスを渡し、実行前の安全検査で拒否された。
fixture側で実パスを指定する修正を行い、runtimeのリンク拒否条件は維持する。

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

### 2026-10-21の実機確認

- インストール済みCLIは引き続き`0.163.0-alpha.10.2`。helpの読取りのみで、新規モデルセッションは開始していない。
- 公式CLIのtag `rust-v0.163.0-alpha.10`が指すcommit
  [`cd643519fb56011557d09b41053a3a4c0e013e1f`](https://github.com/openai/codex/blob/cd643519fb56011557d09b41053a3a4c0e013e1f/codex-rs/cloud-tasks/src/lib.rs)
  で、execは単一task URL、statusは状態を含む人間向け3行、diffは選択したattemptの本文を出力することを確認した。
  execのquery `-`は標準入力のUTF-8本文を使う。statusはREADY以外で終了コード1を返すため、
  非0終了だけで通信失敗と判定しない。READYもテスト合格の証拠にはしない。
- 公開sourceとインストール済みalpha派生版のbinary同一性や、実応答の形式は未検証。
- Browserで`https://chatgpt.com/codex`を開くと、当初Cloudflare確認画面になった。
  本人がクリックを承認した後、ブラウザ操作が中断したため、クリック成立は確認できなかった。
  復元時には確認画面を経ず公式紹介ページへ進み、そこで表示された「Cloudへ移動」
  （`https://chatgpt.com/codex/cloud`）からChatGPTのクラウド選択画面へ遷移した。
- 同画面の環境選択に「公開済みの環境はありません」と表示された。
  これは表示されたアカウント・画面の範囲の観測であり、旧CLI用環境の不存在や新旧互換性の証明ではない。
  環境作成・公開・タスク送信は行っていない。
- 現ホストの有効方針は標準子の利用を指定しており、Cloud CLIで別モデルを起動できる許可は確認できない。
  通常Chatのopt-inや入力JSONを代替許可にしない。クラウド実行を許すホストと実環境でのpilotが残る。
- 認証の必要性を確認するためローカル認証の状態・登録済みhandoffを調べたが、ChatGPT用presetはなかった。
  認証情報や会話履歴は取得していない。今回作成したBrowserタブは閉じ、残存なしを確認した。

### 再開時の手順

1. 実行ホストが許すCodex Cloud専用の入口、対象環境ID、repoへの接続と実行権限を確認する。CLIの存在や申告値だけでは実行可能としない。
2. 対象branchと期待revisionを固定し、クラウドが取得する材料・実行するテスト・返却先・完了条件を引き継ぐ。ローカル未commit変更やローカル専用MCPの自動引継ぎを前提にしない。
3. 元依頼で承認された範囲内の小さな1件を、利用可能な正式な経路で一度だけ投入する。送信結果が不明な場合は照合へ戻し、再投入しない。
4. 同じtask IDの結果を回収し、入力の同一性、実際のrevision、差分、実行環境とテスト結果、未検証範囲を確認する。
5. 内容を受け入れてから、許可された適用先へ反映する。タスク終了・差分取得・ローカル適用・PR作成・マージ・本番反映をそれぞれ区別する。

クラウド環境、Browser経由の実投入、Linux/macOSの実アプリ連携、再開をまたぐ実タスクの回収は未検証。
ローカルテストやGitHub CIの成功は、これらの実証に置き換えない。
