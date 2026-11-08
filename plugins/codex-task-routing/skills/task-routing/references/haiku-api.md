# 任意の Haiku API 経路

`claude-haiku-5-5` / `xhigh` を、通常の実装・調査を含むまとまった工程の候補として検討できます。Lunaの定型工程に限定せず、品質・費用の優劣は推測しません。適任の既存担当とCloud/Proの優先は維持します。標準Codex子とは別の、root所有の経路です。通常Chatのfallbackではありません。既定では無効で、フックは実行しません。

実行は、現在のホストが外部プロセスへの委譲を許し、対象操作とAnthropicへの資料送信が認められた場合だけです。標準子に限定するホストではplanまで。JSONのbooleanは呼出し元が確認した事実の記録であり、権限を作りません。明示opt-in、予算、転送する資料と対象ファイル、操作範囲、Git変更、成果の受入を別々に確認し、既存の承認を同じ範囲で再利用します。秘密・不要な個人情報を依頼や対象に含めず、対象repoにCLIを信頼できることを確認します。既存Claude Code 2.1.286以降と実行時の `ANTHROPIC_API_KEY` が必要で、自動インストール・鍵保存はしません。

実行時の環境は最小限にし、API keyと公式 `https://api.anthropic.com` を指定し、OAuth・他provider・proxy等は引き継ぎません。ただし `--restricted` でも管理者のmanaged settingsは適用されます。管理設定が第三者providerや別endpointへ変更していないことを、秘密を閲覧せず呼出し元が別途確認し、確認不能なら `official_endpoint_verified` をfalseにして実行しません。環境変数の指定だけでは実際の接続先の証明にはなりません。CLIは `--bare --restricted --disable-slash-commands --strict-mcp-config`、セッション非永続化、`dontAsk` と制限されたツールで起動します。CLI permission はOS sandboxではありません。ホスト側のsandbox・権限を維持し、その境界を超える能力を与えません。

## 任意の継続設定

通常の新規セッションでも候補として案内する場合、利用者がCodexホームの `codex-task-routing/haiku.json` を次の内容で明示的に設定します。ファイルがない場合と `enabled:false` はオフです。設定は自動作成・変更しません。

```json
{"schema_version": 1, "enabled": true}
```

`python <plugin-root>/scripts/haiku_config.py status` はenabled・設定状態だけを読み、実行許可は返しません。不正な設定は無効と診断し、他の既定・overrideを変えません。オンの場合、rootのSessionStartだけにtext-only候補と条件を短く案内します。子の起動・ネットワーク・モデル呼出しは行いません。読み込み済みの上位指示は変わらず、反映を確認するには新しいセッションで有効方針とホストの許可を確認します。推薦入力の `execution.haiku.enabled` はこの設定の現在の確認結果を使い、他の能力・承認やroot役割は各々確認します。設定オンだけで資料送信や実行を許可しません。

## 通常推薦からの提案実行と回収

通常の `session_plan.py recommend` は、rootの同一目的の通常以上の作業にもHaikuを候補にします。[推薦の入力と不足条件](session-routing.md#同一目的のhaikuテキスト提案)を確認してください。Windowsでの初期対応は、個別送信が承認された資料だけを使う **toolなしのテキスト提案** です。ファイル読取り・編集・コマンド実行はモデルに与えず、親が出力の検査、必要な編集と独立テストを続けます。適任の既存担当とCloud/Pro優先を維持し、選定やJSONの宣言を実行権限にしません。

対応するlocal-authの提案adapterが現在のホストに導入され、`ANTHROPIC_PROPOSAL_PLAN` が登録済みの固定inbox・vault rootと各canonical pathのSHA-256を指定している場合のみ、同じroot担当が以下を実施します。adapterは固定planを照合し、ユーザー入力の任意pathを作業先として受け付けません。未導入・対応未確認は不足として扱い、このプラグインだけで登録や実行を完了したとは扱いません。認証値の探索や表示はせず、既存のローカル認証経路を使います。

1. 依頼IDを固定し、承認済みの送信資料だけを固定inboxの `<task_id>.input.txt` に準備します。task_idは小文字の英数字で始まり、小文字英数字・`_`・`-`のみの最大64文字です。Windows予約名 `con` / `prn` / `aux` / `nul` / `com1`〜`com9` / `lpt1`〜`lpt9` は使えません。秘密や未承認資料を含めず、送信対象と操作の既存承認を確認します。
2. 同じinboxに `<task_id>.manifest.json` を用意し、schemaは `schema_version: 1`、`task_id`、入力バイト列の `input_sha256` のみです。完成したmanifestのバイト列をSHA-256で照合し、依頼ID・入力hash・manifest hashを固定します。これらの一致は送信承認の代わりにはなりません。
3. 実ホストが明示操作と送信を許す場合に限り、登録済みnative CLIの `--anthropic-proposal <task_id> <manifest_sha256> --vault-root <登録済み固定vault root>` を一度だけ実行します。フック・推薦器・通常Chatのfallbackからは起動せず、キーをCLI引数にしません。経路指定は登録済みinboxとvault rootの範囲に限ります。
4. 固定inboxの `<task_id>.result.json` と `<task_id>.proposal.txt` を元依頼に対応付けて回収し、task_id、input_sha256、output_sha256、モデル、終了理由と受入状態を確認します。要求effortはxhigh、実effortは未観測です。出力は未受入の提案で、rootが意味・対象・差分を確認してから承認済みの編集とテストを行います。

adapterのstdoutは状態・トークン件数・`valuesReturned:false`等のメタデータに限定し、提案本文は固定inboxで管理します。statusの `not_started` / `intent` / `recorded` / `failed` は実行状態であり、意味確認や受入結果ではありません。APIリクエストはtoolなし、入力128KiB、最大出力4096トークン、timeout 120秒です。

待機・失敗・結果不明は同じ依頼を保持し、別IDや別inboxで再送しません。実行結果が不明なときは、同じ登録済みCLIの `--anthropic-proposal-status <task_id> <manifest_sha256> --vault-root <登録済み固定vault root>` で照合します。proposal本文には資料や機密が含まれる可能性があるため、固定inboxの権限・保存先・取扱いを現在のホストで確認します。接続成功、モデル応答、内容受入、親による編集・テストは別の証拠です。

以下のClaude Code CLIによる固定対象の編集実験は別の経路です。Windowsの一般的な無人編集に使える隔離が確認されたことにはならず、通常推薦のtext-only能力として扱いません。

## 利用前の確認

この版のマージやプラグインの配布だけでは、実行中のセッションに読み込まれた上位指示や旧版フックは変わりません。利用するホストで、ユーザーの明示opt-inに加え、ホスト自身がこの別経路の実行を許すことを確認します。必要な方針変更・プラグイン更新・フックの確認は利用者または管理者が通常手順で行い、新しい方針が読み込まれたセッションで確認してください。現在のホストが標準子のみと定める場合は、旧方針を迂回せず実行しません。

APIキーの有無が不明なら、本人が [Anthropic Console のAPIキー画面](https://platform.claude.com/settings/keys) で発行状況と利用可能な権限を確認します。キーの値をチャットに貼らず、承認済みの実行プロセスの環境変数 `ANTHROPIC_API_KEY` として渡してください。このプラグインは既存キーを探索・発行・保存せず、接続される環境を自動登録しません。別のPowerShellを本人が開ける場合は、例えば次の入力で画面にキーを表示せず、そのPowerShell内の後続コマンドへ渡せます。APIキーを扱うPowerShellは本人が管理し、使い終えたら閉じてください。

```powershell
$env:ANTHROPIC_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host -AsSecureString 'Anthropic API key')).Password
```

`doctor` はrequestの明示opt-inと宣言済みの不足、CLIの版とhelp、repo root・branch・HEAD・clean状態、環境変数名の存在だけを表示します。キーの値は読み出しません。環境変数が空でも名前は存在し得るため、この表示は有効なキーの証明ではありません。実runは空文字・空白のみを拒否します。ホスト権限・承認・管理設定・キーの有効性・API接続は検証できず、`execution_authorized` は常にfalseです。CLI helpで必要なflagを確認できない場合や、隠し `--max-turns` のhelp parserが拒否した場合は実run前に止めます。parserの受理は実際のturn制限の実証ではありません。doctorはCLIの版とhelpのローカル表示だけを実行し、鍵を子プロセスに渡さずモデルへの依頼を行いません。信頼できる公式CLIを明示し、ホストのネットワーク制限も維持してください。

## 入力と実行

UTF-8の依頼ファイルとJSON requestを用意します。次は形式の例であり、値は対象・承認に応じて設定してください。`input_sha256` は依頼ファイルのUTF-8バイトのSHA-256、`cwd` は絶対パスのrepo root、`head` は完全なcommit IDです。既存のdirty作業と混ざらないよう、この版はcleanなbranchだけを開始できます。必要な指示や判断材料は依頼文に明示します（`CLAUDE.md`等は自動読込みしません）。

```json
{
  "schema_version": 1, "enabled": false, "actor": "root", "request_id": "example-task-1",
  "input_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "cwd": "/absolute/repository", "branch": "codex/example", "head": "0000000000000000000000000000000000000000",
  "tools": ["Read", "Glob", "Grep", "Edit"], "bash_rules": [],
  "max_turns": 10, "max_budget_usd": 2.0,
  "host_execution_allowed": false, "target_operations_allowed": false,
  "materials_transfer_allowed": false, "official_endpoint_verified": false
}
```

`tools` は `Read`, `Glob`, `Grep`, `Edit`, `Write`, `Bash` から明示します。テストを任せる場合だけ `Bash` と、例えば `"bash_rules": ["Bash(python -m unittest *)"]` のような承認済みコマンド範囲を指定します。無条件のBash、任意の構文やMCP・skill・子エージェントは指定できません。Bashルールのパターンは完全なコマンド封じ込めを保証せず、信頼できるコード・依存関係を対象にし、API keyを含む子環境がテストから見え得ることに注意してください。シェル操作や対象範囲はホストの実際の制限と承認に従います。

```text
python <plugin-root>/scripts/haiku_route.py inspect --request request.json
python <plugin-root>/scripts/haiku_route.py plan --request request.json
python <plugin-root>/scripts/haiku_route.py doctor --request request.json --claude-executable <Claude-Code実行ファイルの絶対パス>
python <plugin-root>/scripts/haiku_route.py run --request request.json --input task.txt --state-dir <共通の既存ディレクトリ> --claude-executable <Claude-Code実行ファイルの絶対パス>
```

出力本文を保存しない連携では `run` に `--metadata-only` を付ける。既存のclaim、入力hash、リポジトリ、承認宣言、モデル・権限拒否の検査は同じで、結果ファイルには固定metadataだけを保存する。APIキーの直文字列が入力またはCLIの標準出力・標準エラーに現れた場合は拒否し、発行済みclaimを保持する。変形・分割された秘密やCLIが直接作成したファイルまで検知・隔離する保証ではない。省略時の従来の結果保存動作は変わらない。

inspect/planは接続・認証確認・プロセス起動を行いません。runは実行直前にCLI版とhelp、入力hash、repo root・branch・HEAD・clean状態を確認し、状態ディレクトリに排他的なclaimを書いてから一度だけ起動します。全依頼は同じ保護された共通state rootを使い、別ディレクトリでのclaim重複は検知できないため切り替えて再送しません。途中失敗やtimeoutは結果不明として保持し、元のrequestと対象の状態を照合してrootが判断します。成功応答を保存しても自動受入せず、モデル利用情報が指定modelのみと確認できない場合や権限拒否があれば部分／未確認です。`xhigh`は要求値で実effortは未観測です。結果JSONは依頼の内容や機密を含み得るため、state rootへのアクセスを本人が制限します。鍵とstderrはアダプターが保存・表示しません。

失敗時は固定の分類と終了コードだけを別ファイルに残すよう試みます。記録失敗時もclaimは保持します。timeout時にPythonが停止するのはCLIプロセスで、Bashなどの子プロセスの停止・後処理までは保証しません。再開や後片付けの前に、対象のプロセスと作業状態を確認してください。

`max_turns` はCLIへの指定、`max_budget_usd` はCLIの推定額の上限であり、最終請求額の厳密な上限ではありません。結果・差分・テストと実際の請求をそれぞれ確認します。実API、各OS、managed settings、権限ルールの動作は別途実環境で検証が必要です。

## 合成リポジトリでの確認

次のprepareはAPI接続・課金を伴わず、既存でないディレクトリに小さなGitリポジトリ、失敗する加算関数、独立テスト、依頼文、無効状態のrequestとstateを作ります。既存ディレクトリの再利用・上書きはしません。テスト対象repoとstateは分離されています。

```powershell
$smokeDir = '<新規の合成fixture用絶対パス>'
python <plugin-root>/scripts/haiku_smoke_fixture.py prepare --output-dir $smokeDir
python <plugin-root>/scripts/haiku_route.py doctor --request "$smokeDir/request.json" --claude-executable <Claude-Code実行ファイルの絶対パス>
```

現ホストの実行許可、利用できるAPIキー、対象操作と資料送信の承認、および予算を確認できた場合だけ、その確認に一致する既存requestのbooleanを手動でtrueにし、同じプロセス環境のキーと、固定の `"$smokeDir/state"` を使って上記 `run` を一度だけ実行します。キー・予算・権限が不明なら実行せず、doctorと合成準備までで止めます。失敗や結果不明ではclaimを保持し、別stateへの再送はしません。実行後は次の独立検証を行い、指定ファイルだけの変更と元のテスト通過を確認します。

```powershell
python <plugin-root>/scripts/haiku_smoke_fixture.py verify --output-dir $smokeDir
```

verifyは同じfixtureの変更済みPythonコードを実行するため、ホストの隔離・承認が必要です。API keyは独立テストの子プロセスへ渡しません。合成fixtureや模擬テストの成功は、実API接続・課金・モデルの動作・ホストの許可を証明しません。実run後もclaim、結果、差分、テスト結果、モデル情報を分けて確認し、本人が保存・整理を判断してください。

参照: [Claude Code headless](https://code.claude.com/docs/en/headless)、[CLI reference](https://code.claude.com/docs/en/cli-reference)、[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)。Agent SDKは将来のcallback拡張候補で、この版では使用しません。
