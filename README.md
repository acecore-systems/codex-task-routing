# Codex Task Routing

調査・文書・画像・データ処理・運用・開発の仕事を、必要な判断能力に合わせてCodexの標準サブエージェントへ振り分けるプラグインです。

開発時の作業・検証・配布の方針は [AGENTS.md](AGENTS.md) を確認してください。

Acecoreが日常利用で改善している分担原則を既定値として同梱します。親のモデル・effortを維持し、引継ぎ、同じ担当の継続、重要なレビューまで含めて、品質と総利用負担を考慮します。次回送信のモデル選択を勧める機能はありません。節約率は未実証です。

推奨する開始設定は **Sol/max** です。親は利用者が選びます。難しい判断や実装を伴う調査では、分離できる証拠収集をLunaに任せ、判断が必要な点を親に返します。Sol親からSol子への委譲は、難題のまとまった工程を分けることで具体的な利点がある場合に限ります。

| 担当 | 既定 | 役割 |
| --- | --- | --- |
| 親 | 推奨 Sol/max | 利用者が選択。判断・優先順位・受入を担当 |
| 子 | GPT-6 Luna/max | 調査・証拠収集 |
| 子 | GPT-6.1 Terra/high | 通常の実装・修正 |
| 子 | GPT-6.1 Sol/high | 難題の実行 |
| 子 | GPT-6 Astra/xhigh | 必要な限定的専門判断 |
| 別経路 | 通常Chat Pro | 許可・資料・アクセスがそろう相談の第一候補 |
| 任意の別経路 | Haiku API / xhigh | 明示opt-in、ホスト・対象・資料送信・local-auth adapterの条件を満たす提案 |
| ローカル認証経路 | local-auth-lfm | 秘密を扱う登録済み認証工程 |

適合する新規の独立repo工程はCloudを先に検討します。既存担当と明示された有効override、Chat所有者・階層・認証・承認の境界は維持します。

調査→修正→テスト→明示承認済みPR→合意した対象の指摘対応→必要な再検証を、同じ担当の完了単位にします。通常の追加指示や範囲内修正のたびに新しいSol/Terraを起動せず、最新revision・差分・到達点・残条件を保持して継続します。合意済み設計の変更、新たな権限、範囲拡大、重大な影響、未解消の矛盾、検証不能は親へ返します。未到着のレビューを対応済みにせず、PR作成の承認からマージ・本番反映を推定しません。

「確認」「調査」という入口の語だけでなく、受入条件と完了までに必要な工程から担当を選びます。既定方針では、テスト・失敗の切り分け・通常修正・再試験を含む普通の検証はTerra/high、難題はSol/highを所有者の候補とします。標準階層・アクセス・並列または直列の委譲条件がそろう場合、TerraまたはSolの担当自身が、分離できる証拠収集だけをLuna/maxへ渡せます。常に子や孫を起動する意味ではなく、有効なoverrideとホスト制約を優先します。

ローカル経路は一般的な実装・文章作成の振り分け先ではありません。実際のモデル・対応工程・本人操作待ちはMCPで確認し、接続済み・受付済みだけでは認証成功と扱いません。

0.26.0は、別途インストール済みClaude Code CLIによる[任意のHaiku API経路](plugins/codex-task-routing/skills/task-routing/references/haiku-api.md)を追加します。既定オフ、root所有で、標準子に限定されたホストでは計画までです。API実行や実費、品質・能力比較は未検証です。

旧`terra`キーはschema 1の既存overrideの読取り互換用です。分担候補には出しません。新しいTerra 6.1の候補キーは`terra61`で、旧overrideを自動移行しません。実際のモデル利用可否・対象適合・既存担当の継続を確認し、性能・費用の優劣は推定しません。同モデルへの無用な移管を避けます。標準孫は割当て役割としてAstra子→Sol/Terra/Luna、Sol子→Terra/Luna、Terra子→Lunaまで。Solはホストの並列・直列許可と深さを満たす場合に通常実装をTerraへ委譲し、成果をレビュー・統合します。Terra→Sol、Luna子・孫から先と同モデル/上位役割は禁止し、役割・深さ不明はrootへ返します。これは一般的なモデル性能順位ではありません。min/max設定は互換性のため残し、自動選定は有効なdefault_effortに固定します。既存overrideと他モデル親は保持します。並列分担には委譲する担当自身の独立作業が必要で、直列の専門委譲は具体的な追加価値とホストの許可を確認した場合だけ使います。

## 導入

必要な環境は次のとおりです。

- Codexのプラグイン、ライフサイクルフック、標準サブエージェントが使えるローカル環境。
- `python --version` がPython 3.11以上を返すこと。Python標準ライブラリだけを使います。
- 選択したモデル・effortが、そのアカウントと実行環境で利用できること。
- 任意の自動更新にはCodex CLIとGitが必要です。定期実行の登録はWindowsに対応し、Windowsタスクスケジューラと同じPythonの`pythonw.exe`を使います。

次のコマンドで `main` の配布版を導入します。複数PCで同じ版を固定する場合は、`main` の代わりに同じcommit SHAまたは公開タグを指定してください。

```text
codex plugin marketplace add acecore-systems/codex-task-routing --ref main
codex plugin add codex-task-routing@codex-task-routing
```

ローカルのcheckoutを使う場合は、リポジトリ直下で実行します。

```text
codex plugin marketplace add .
codex plugin add codex-task-routing@codex-task-routing
```

導入後、CLIの `/hooks` でこのプラグインのフックを確認して信頼し、新しいタスクを開始してください。フックの定義を変更した版は、再確認が必要な場合があります。スキルの検出だけでは常時適用されません。[公式フック仕様](https://learn.chatgpt.com/docs/hooks)

0.22.0で`PreToolUse`フックが加わり、フック定義が変わりました。0.21.1以前の版から更新した場合は、更新後に`/hooks`で一度再信頼してください。任意の同モデル委譲ガードは既定でオフのため、オフのままなら何も出力せず、何も書きません。有効化する場合は「任意機能」の「子からの同モデル委譲を検出・拒否する」を参照してください。

0.31.0で、親の`Bash`・`exec`・`exec_command`の呼出しに、100KB以上のファイルを`cat`・`type`・`Get-Content`などで丸ごと読む場合の注意を返す`PreToolUse`フックが加わりました。止めず、範囲指定や委譲を案内するだけです。フック定義が変わるため、更新後は`/hooks`で一度再信頼してください。

## 適用確認

新しいタスクの開始時に有効方針の版、policy hash、参照先が渡されることを確認します。開始・再開・コンパクション時に有効方針を読み込み、子には短い引継ぎ用のコンテキストと参照先を渡します。フック自体はモデル呼出し・ネットワークアクセス・子の起動を行いません。

設定と競合はリポジトリ直下で診断できます。

```text
python plugins/codex-task-routing/scripts/routing.py status --json
python plugins/codex-task-routing/scripts/routing.py render --output-dir outputs/effective
```

`status` は設定・版・上書き・競合の診断です。ホストのフック信頼や実行モデルを推測して「有効」とは判定しません。実際の開始時に方針が渡されたことと、子の起動時のツール・実行結果を別に確認してください。最初の検証対象はWindowsとCodex CLIであり、設定の要求値だけでは実際の子がそのモデルで実行した証明になりません。

0.24.0では、まとまった調査・実装の前に担当を決め、適合し許可された場合は実際に委譲します。計画や推薦だけを委譲済みとは扱いません。親が継続する場合は、短い作業、受入、既存担当、本人の禁止、ホスト制約などの具体的な理由を一度示します。カスタム`principles.summary`は変更せず、モデル・役割の判断前に`effective.md`を確認します。詳細を読む分の追加負担は残ります。

0.25.0では、ローカル現物が必要なことと標準子のアクセス可否を分け、並列条件のためだけに通常実装を親に残さない方針を追加しました。実際の独立作業がなければ並列を強制せず、直列はホストの許可と追加価値を要します。既存のカスタムsummaryは既定summaryを置換し、新しい既定文を自動では受け取りません。導入や実運用の効果は別途確認が必要です。

0.27.0では、制作の素材構成・編集・表示確認を完了単位として引き継げるか検討します。作業の内部が密結合していることだけでは、親が担当し続ける理由になりません。rootの各`UserPromptSubmit`を識別できた場合、最初の配送で短い条件付き案内を返します。短い会話や適任の既存担当、ホスト制約を尊重し、本文を読まず委譲も強制しません。重複配送と子のイベントでは案内を返しません。毎ターンの案内には追加のコンテキスト負担があり、実配送・実委譲の成功は別途確認が必要です。

0.28.0では、親が既に把握・着手したことだけで仕事を親に残さず、将来の編集や試行・検証の文脈分離を引継ぎ便益に含めます。対象・素材・決定・制約・受入・権限を短く渡し、適合し許可されるまとまった仕事は実際に委譲します。適任の既存委譲先や具体的な親専用操作、上位制約は尊重します。計画helperでは親か委譲先かを明示した新しい入力を使え、旧入力は互換動作のままです。

親の作業が同じターン内で続いた場合、対応するrootの`UserPromptSubmit`と作業系`PreToolUse`が識別子で一致し、4件の異なる呼出しに達すると一度だけ助言を返します。子開始、稼働中の子、既存担当への連絡を観測した場合も抑制せず、担当範囲とアクセスの確認、および適任の既存担当への返却を案内します。spawnの試行だけでは子の開始扱いになりません。wait_agentの待機は連絡や作業として扱いません。明示的な親の`apply_patch`は子開始や連絡だけで抑制せず、担当の範囲確認を案内します。`write_stdin`は待機と作業を区別できないため回数に含めません。助言は処理を拒否せず、上記の正当な例外や実際のホスト制約を優先します。依頼内容・タスクの大きさ・成功した委譲は判定しません。対応外のツール、ラッパー内部の呼出し、失われたイベント、異なる識別子、子の終了通知欠落では検知漏れや不要な助言があり得ます。

`python <plugin-root>/scripts/parent_guard.py status` はローカルの保持状態を読み、`unobserved`、`recorded`、`unavailable`を区別します。`reminders`は作業系呼出し時の助言数で、入力時の案内数ではありません。`recorded`や助言0件は、フックの信頼・全イベント受信・適切な委譲・節約の証明ではありません。壊れた状態や実行不能時も処理をブロックせず、診断で確認します。識別子はハッシュ化し、固定の状態と件数のみを専用SQLiteへ保存します。本文・コマンド引数・モデル名・パスは保存せず、保持対象は7日・最大512ターンに制限します（次のprompt時に整理）。変更したフック定義は`/hooks`で再確認してください。実ホストのイベント配送と助言反映は未検証です。[検証記録](docs/validation-0.27.0.md)

`turns_with_observed_child_start` は、同じセッションで親ターンが開いている間に子の開始を観測したターン数です。`UserPromptSubmit` で親ターンを開き、同じ親ターンの `Stop` または `Interrupt` で閉じます。集計は開始を観測した時点の紐づけを示します。旧方式で記録された委譲フラグはこの集計に含めません。起動を指示した操作や使用量の確認には別の根拠が必要です。

子の開始と終了を観測した場合、他の子が動作中でなく担当への連絡も観測されていなければ、次の親の作業前に一度、残る通常検証・修正・再試験を同じ適任者に戻せるか案内します。未完了の判定や操作の実行はしません。イベント欠落や担当連絡の識別の限界があるため、案内の有無は担当状況の証明ではありません。[検証記録](docs/validation-0.30.1.md)


## 設定を変更する

上書きは `$CODEX_HOME/codex-task-routing/overrides.json` へ保存します。`CODEX_HOME` が未設定なら `~/.codex` です。上書きがなければ作者の既定値を使います。旧版の`luna`・`sol`の`id`を5.6系や旧`gpt-6-sol`へ固定した上書きは自動で書き換えないため、移行時に`status --json`の適用キーを確認してください。Solの旧ID指定は`gpt-6.1-sol`へ変更してください。プラグインの更新・解除でこの上書きファイルを編集・削除しません。

`status --json` の `configured_child_models` で、上書き適用後の子のモデルIDと固定effortを確認できます。これは実行時の指定値であり、実際にそのモデルで動いた証明ではありません。親の実設定は `host.runtime_model` / `host.runtime_effort` と区別し、取得できない場合は `unknown` のままにします。

Sol子を配布版の既定値変更にかかわらず `max` に固定する場合は、次のように指定します。

```json
{
  "schema_version": 1,
  "models": {
    "sol": { "default_effort": "max", "max_effort": "max" }
  }
}
```

Terraの個別設定は新キー`models.terra61`に指定します。`principles.summary`等の文章overrideは更新後も古い内容を保持し得るため、`status --json`の`override_review`と両フックの案内に従い、新しい候補表・役割・Chat境界との整合を確認します。設定や稼働プラグインは自動で書き換えません。0.21.0の検証範囲は[検証記録](docs/validation-0.21.0.md)を参照してください。

これは使い方の例で、既定値を下げる推奨ではありません。原則自体も `principles.summary` や対応する詳細節を置き換えて変更できます。[設定の詳細](plugins/codex-task-routing/skills/task-routing/references/configuration.md)

## 任意機能

### Drex に定型判定を割り当てる

公開・合成テキストの分類、条件判定、基準付き採点を、親が Drex Max v1.1 に割り当てられます。既定は無効です。対応する Local Auth を用意し、[Drex の設定と実行手順](plugins/codex-task-routing/skills/task-routing/references/drex-decisions.md)に従って有効にします。

### 認証工程をローカルへ引き継ぐ

パスワードやAPIキーの入力・保管・転送、OAuth JSONやCookieを扱う工程は、通常のモデル選定より先にローカル経路を確認します。開始・再開フックがこの方針を渡すため、各タスクに個別の引継ぎ文を貼る手間を減らせます。認証方式の公開仕様や秘密を含まないコードは、通常の分担で扱います。

別途導入済みの`local-auth-lfm` MCP（受付protocol `0.3.0`）を使い、`local_auth_status` → `local_auth_handoffs` → 登録済みpresetで依頼・準備確認 → 承認済みの対応工程を専用ツールで実行、という順で進めます。実行側の単一モデルと共有runtimeを使うため、Task Routingにモデルを追加保存しません。ツール未提供・非対応protocol・preset不足・未実装処理は、その不足を報告します。

受付登録、本人操作待ち、実ログイン/API検証を区別し、秘密値・Cookie・認証画面はクラウドへ返しません。フックは秘密ファイルを読まず、MCPやモデルを起動しません。アプリ起動承認や本人確認を含む既存の承認条件を維持します。`chat_plan.py`でも`requires_private_auth: true`を指定すると、Chatの選定より先に`local_auth`を返します。分類だけで実行を承認する機能ではありません。[ローカル認証の手順](plugins/codex-task-routing/skills/task-routing/references/local-auth.md)

### セッションとクラウドの候補を選ぶ

担当/model/effortと、実行環境の選択を分けます。同じ完了単位を実行中の適任担当は修正・検証・受入まで継続します。「親もできる」という理由だけで新規の独立repo実装・テストを親へ固定せず、適合するCloudをProより先に選びます。短い密結合の仕事は継続、同一目的の限定補助は標準子、別目的・独立成果物・別repo/PR・独立継続管理は別セッション、ローカル専用の環境が必要ならローカルを提案します。長時間・長文脈・利用枠回避・同モデル移管だけでは分割しません。

0.16.0の`session_plan.py recommend --facts <facts.json>`は候補の事前指定なしで目的単位から比較し、理由・代案・不足・引継ぎ項目・次の操作を返します。通常会話で短く提案してもよく、毎回helperを実行する必要はありません。Cloudの環境・材料・必要テスト・host条件が未確認なら条件付き提案に留め、利用不能ならその理由とローカル等の代案を残します。適合する独立repo工程はCloud優先です。相談は、承認済みChatの材料と実ツールで完結すればProを優先し、調査や実装は役割に応じて選びます。repoの既存環境を再利用し、未確認は調査、確認済み不存在は対応UIで作成・準備・テスト・公開へ進みます。既存環境からも独立目的は新セッション、同じ完了単位は元担当へ継続します。同一repoの複数セッションも、独立範囲・branch/workspace分離・統合担当・委譲者の独立作業・ホスト許可がそろえば並列利用できます。衝突する変更は開始前に調整します。たとえば「別repoの回帰修正は実装とテストまでCloudへ分ける案です。ローカルセッションも候補です。対象環境と材料転送範囲が未確認なので、まず既存承認と環境を確認します」と説明します。[入力例と境界ケース](plugins/codex-task-routing/skills/task-routing/references/session-routing.md#目的単位から推薦する)。

`session_plan.py`は提案と、クラウドのinspect / dispatch-plan / receive / acceptを機械照合するローカルhelperです。ネットワーク・モデル・CLIを起動せず、タスク作成や送信を行いません。CloudはUIの`codex_cloud_ui`と明示handoff、または観測済み既存Codexタスクへのnative継続を使い、元依頼の有効な同一範囲の承認を再利用します。送信前の永続claimと依頼ID/hash・所有者・環境・元タブ/taskを保持し、送信済み/結果不明は再送せず元taskを回収します。クラウドは通常Chat opt-inと別です。新規トップレベルセッションは本人の明示依頼と現ホスト条件を確認し、model/thinkingの既定を維持します。

Cloud依頼本文に、絶対cwd・repo/branch/期待revisionの照合、README・AGENTSの読取り、cloud-environment runtimeスキルとnetwork policyの確認、必須検証、許可操作・停止条件を明記します。`start_skill`の自動注入を前提にせず、HTTP listenerはUIの利用要件にしません。以前の版での実タスク起動・回収・テスト成功は過去記録とし、確認した版・環境を越えて成功を一般化しません。[標準手順と入力契約](plugins/codex-task-routing/skills/task-routing/references/session-routing.md)、[0.18.0の検証範囲](docs/validation-0.18.0.md)。

0.15.2ではCloudを現行UI専用としました。0.18.0は、これに既存Codex durableタスクのnative継続・回収を別契約で追加します。旧Cloud CLIの実行コード・入力受付・専用資料は配布しません。UI用の最小ローカルclaimは残し、依頼ID・材料hash・scope・元タブ/task、排他と巻戻し拒否を保持します。既存の保存記録は削除・変換せず、旧形式・未知・破損したclaimがあれば照合まで新規送信を止めます。

実運用の順序は[Cloudセッション運用手順](docs/cloud-session-workflow.md)、新規UI作成・native継続・Cloudテストの実確認範囲は[0.19.0の検証記録](docs/validation-0.19.0.md)を参照してください。

### 既存Cloudへ直接継続する

0.18.0の`session_plan.py cloud-native`は、確認済みの既存Codexタスクを固定し、正式な`send_message_to_thread`による継続と`read_thread`による回収を計画します。新規作成APIは追加せず、Workや旧CLIへ切り替えません。host/task・scope・依頼hashを固定したメッセージごとの永久claimを残し、前依頼や結果不明を別IDで迂回しません。wait未確認・失敗は同じ元taskのreceive計画へ戻します。

helperは送信しません。rootが新方針と現在のホスト許可・対象承認を確認し、新規claimに対応する正式ツールを一度呼びます。旧UI-only指示が注入中のターンでは実送信せず、新方針を読み込んだホストで確認します。ツールschemaの存在、既存タスクの読取り成功、継続送信、実テスト成功、内容受入を別々に記録します。[入力例と正式操作の手順](plugins/codex-task-routing/skills/task-routing/references/native-cloud.md)。

### 通常Chatの一時Chat（Pro）へ渡す

`Pro`はモデル選択欄の値です。旧`6 Pro`/`GPT-6 Pro`の設定・観測も互換として受理し、既存設定を自動書換えしません。契約バッジやAPIモデルIDは代用できず、実行backendは確認できなければ不明です。0.12.0では既存の有効な転送確認に`transport_evidence`を重ねて要求せず、Browserのinspect/send/receiveを個別に扱います。`--phase inspect`で確認操作、既定のdispatchで一度の送信、`--phase receive`で再送なしの既存回答回収を分けます。未確認・明示false・実際の拒否を許可済みに変換しません。表示と実行の違い、公式・ホスト・repoの制約、不足条件の担当と解消手順は[許可判定の詳細](plugins/codex-task-routing/skills/task-routing/references/chat-authorization.md)にまとめています。

通常Chat内の接続済みGitHubで、実際の権限と実行環境がそろえば、調査・修正・テスト・PR・CI確認・明示承認済みのマージをまとめて扱えます。この直接作業にCodexから別Chatへの転送許可は不要です。一方、Codexから別Chatへ送受信する場合の未確認許可を、ネイティブGitHub操作の成功で代用してはいけません。


通常Chat経路は明示的に有効にした場合だけ使います。root直下Sol子はbrowser-temporaryで依頼の準備・送信・回収・内容受入・承認済み修正まで所有できます。Astra/Luna子と孫からChatは使えず、legacy経路はrootによる非送信回収専用です。SubagentStartだけで役割や深さを推測せず、元の引継ぎ・現在のホスト許可と照合します。Sol→Proは標準Codex孫と別経路です。所有者が変わっても同じタブ/依頼ID/input hashと送信済み状態を引き継ぎ、再送しません。確認済み接続の操作・範囲・有効期限をローカル台帳に保持し、`chat_plan.py` が必要な項目だけ機械照合します。分類専用のAI呼出しや全接続の再調査を避け、通常の承認済み送受信は `chat_transfer.py` と同梱 `chat_browser.js` の `runProHandoff` を対応Browser REPLで実行し、準備済み本文と最終回答をBrowser内の変数のまま受け渡します。親のモデルへ全文を出力して再転記する必要を減らします。[接続と分類](plugins/codex-task-routing/skills/task-routing/references/chat-capabilities.md)、[転送補助](plugins/codex-task-routing/skills/task-routing/references/chat-transfer.md)。これらは明示実行の補助で、フックから起動・送信しません。

通常ChatのProはWork/Codexと別枠ですが、親の準備・操作・検収はCodex側に残ります。Proには契約別の上限があり、無制限ではありません。モデルや残量は実際のUIで確認し、第三者ツール固有の料金まで無料と扱いません。[公式の利用枠](https://help.openai.com/en/articles/20001354)、[Work/Codexの共有枠](https://learn.chatgpt.com/docs/pricing)。

相談は有効なrootまたはroot直下Sol子のbrowser経路で、許可・資料・実アクセスがそろう場合にProを優先します。調査はLuna、通常実装はTerra、難題の実行はSolを候補とし、必要なときだけAstraを検討します。既存担当と認可境界を維持します。

Pro $200の週次枠を積極活用する運用ですが、契約価格だけで経路を有効化しません。[最新上限・送受信方法の許可・比較計画](plugins/codex-task-routing/skills/task-routing/references/chat-limits.md)を確認してください。送信直前は`chat_plan.py --live`で新しい観測を照合し、上限・モデル変更・許可不明で停止します。自動抽出に関する例外の許可は本プラグインでは確認していません。

新規依頼には、目的・材料・合格条件に加え、`handoff` で必要情報、出典要件、鮮度、許可ツール、返却形式、停止条件を渡します。MCPはChat側で必要な操作が使えるかを確認し、回答に実取得の出典・版や日時・取得範囲を求めます。設定済み・選択済みだけを取得成功と扱いません。本文だけの仕事はツールなしで完結させ、承認済み範囲の確認を繰り返しません。

`<CODEX_HOME>/codex-task-routing/chatgpt.json` に次を保存します。プラグインキャッシュ内には保存しません。

```json
{
  "schema_version": 1,
  "enabled": true,
  "required_model": "Pro",
  "transport": "browser-temporary"
}
```

新しいタスクのフックに `Opt-in ChatGPT Chat route is enabled` が表示されることを確認します。`enabled` を `false` にすると、この経路への新しい依頼を停止できます。切替後は新しいタスクから確認してください。進行中のChatを自動停止する設定ではありません。設定を有効にしただけでメッセージは送信されず、接続先の権限も増えません。

この経路には、通常Chatへログイン済みのBrowserと、UIで選択・確認できる `Pro` が必要です。タスクごとに一時Chatを作り、Chatであること、一時Chatであること、`Pro` 表示を送信前後で確認します。準備済みの実依頼を一度だけ送信し、最終回答を依頼IDと材料hashで照合してから必要な結果だけを保存し、一時Chatは保存せず閉じます。同梱runnerが本文の全文一致を確認し、送信は一回だけ行います。待機が続く場合は同じタブと依頼の情報で受信のみ再開します。モデルAPIキーは使わず、WorkやモデルAPIへ暗黙に切り替えません。バックエンドのモデルIDは取得できないため、UI表示と実行メタデータを区別します。

一時Chatでは `send_message_to_thread` を使いません。内部で `isTemporaryChat: false` となるためです。既存の `"transport": "codex-app-tools"` 設定は書き換えず、新規送信を停止し、rootによる既存依頼の非送信回収のみを残します。既存の承認済みMCPは材料の受け渡しに使えます。独自MCPサーバーの公開・書き戻し・自動ファイル添付は含みません。通常Chatの生成はChatの枠、親の処理はCodexの枠を使い、接続先サービスやサーバーの費用は別途確認します。[運用手順・制約](plugins/codex-task-routing/skills/task-routing/references/chatgpt.md) と [browser不要化の課題](docs/backlog.md) を参照してください。

### 使用量の振返りを残す

利用者が明示開始した有限campaignで、通常作業の全範囲実測3件を目標に最大6試行します。欠測は試行数に数え、実測の成功には数えません。開始時フックは状態を知らせ、再開や子の仕事は同じ作業にまとめます。設定確認・監査・導入作業は対象外です。campaign未開始時や終了後、版更新時に自動開始しません。

記録には親子の要求設定と確認できた実行設定、使用量の対象範囲、検証結果と手直しを残します。使用量を取得できない項目は理由付きの欠測になります。記録が閉じても、実測が揃ったとは限りません。会話ログ・DBを自動収集する仕組みや、毎回の計測用モデル実行はありません。

```text
python plugins/codex-task-routing/scripts/routing.py observation status --json
```

記録はCodexホームのプラグイン専用領域へ保存し、リポジトリや公開用ファイルへ会話内容を追加しません。入力例と状態の意味は[観測手順](plugins/codex-task-routing/skills/task-routing/references/observation.md)を参照してください。明示したcampaign・対象rootの次の依頼をarmし、登録対象のrequest counterとthread累積値を照合する手順があります。開始・終了の境界や収集範囲は別途根拠が必要で、利用料・節約率は自動算定しません。証拠がある前後の観測記録は `compare_observations.py --input <comparison.json>` で手動比較でき、親と親子合計、Chat、品質・手戻りを分けます。[入力例と比較条件](plugins/codex-task-routing/skills/task-routing/references/observation.md#根拠付きの前後比較)を確認してください。

### 子からの同モデル委譲を検出・拒否する

既定でオフの任意機能です。子のエージェントが親と同じモデルを明示して`spawn_agent`（Agentツールを含む）で委譲する場合を、PreToolUseフックで検出します。0.22.0でフック定義が加わるため、更新後に`/hooks`で一度再信頼してください。オフのままなら何も出力せず、何も書きません。

有効化するには、利用者が次のファイルを自分で作成・編集します。保存先はプラグインのキャッシュ内ではなく、`<CODEX_HOME>/codex-task-routing/spawn-guard.json`です。自動作成や自動編集はしません。

```json
{
  "schema_version": 1,
  "mode": "observe"
}
```

`mode`は次の3つです。ファイルがない、JSONが不正、未知の`mode`、安全でないパスは、すべて`off`として扱います。

- `off`: 既定です。何もしません。
- `observe`: 出力せず、判定結果を記録します。拒否はしません。
- `enforce`: 同モデルと確定したときだけ、固定の理由で拒否し、同じ記録を残します。それ以外は素通しします。

切り替えは新しいタスクから確認してください。

**対象:** 呼出し元が子のときの`spawn_agent`だけです。Codexは子からの呼出しにだけ`agent_id`を付けるため、rootからの起動は対象外で、記録もしません。

**判定:** 呼び先の`model`引数が呼出し元の`model`と（前後の空白を除いて）完全に一致し、`agent_type`の指定がない場合だけ「同モデル」と確定します。次は素通しします（判定不能、または非同一）。

- `model`の省略: 継承・既定モデル・役割設定で変わり得るため。
- `agent_type`の指定: 役割設定が`model`を上書きし得るため。
- 型不正、入力欠落、異なる`model`。

上位役割かどうかは判定しません。`agent_type`は設定上の役割名で、このプラグインの割当て役割とは別です。深さも入力にありません。判定するのは、呼び先が明示した`model`引数と呼出し元の`model`が一致するかという事実だけです。

**記録:** `observe`と`enforce`では、`<CODEX_HOME>/codex-task-routing/spawn-guard/events.jsonl`に1判定1行を追記します。記録する項目は、schema_version、UTC時刻（分まで）、mode、verdict（`same_model`、`not_same_model`、`undetermined`のいずれか）、固定の理由コード、`would_deny`です。モデル名、agent_id、session_id、パス、本文は記録しません。128 KiBに達すると以後の記録を止めます。削除や回転はしません。

`would_deny`と判定不能の理由の件数は、ガードが何を拾い、何を拾えないかを確認するためのものです。実使用量の取得ではありません。集計は読取専用で確認できます。

```text
python plugins/codex-task-routing/scripts/spawn_guard.py status --json
```

`mode`、`total`、`by_verdict`、`by_reason`、`would_deny`、`capped`を返します。

**限界:** PreToolUseは公式に完全な強制境界ではなく、ガードレールとされています。WebSearchなどフック対象外のツールや、他の経路での委譲は止められません。公式実装では、openai/codexのmain（rust-v0.165.0時点）のhooks schemaとcore実装を確認しています。ただし、実機のCodexでのPreToolUse入力例、denyの反映、導入先のCodexが`agent_id`を渡す版かは未確認です。他OSも未確認です。

### 普段の起動方法のまま自動更新する

Windowsで自動更新を一度登録すると、ログオン中に15分ごとに登録したGit refを確認します。`--ref main`で導入した場合はmainを追従し、固定したcommit SHAは維持します。**専用ショートカットは不要です。普段どおりCodexを開いてください。**

プラグイン導入後、PowerShellで次を一度実行します。リポジトリのクローンは不要です。

```powershell
$routingPlugin = (codex plugin list --marketplace codex-task-routing --json | ConvertFrom-Json).installed |
    Where-Object pluginId -eq 'codex-task-routing@codex-task-routing'
python (Join-Path $routingPlugin.source.path 'scripts/install_updater.py') install
```

リポジトリのcheckoutがある場合は`python scripts/install_updater.py install`でも登録できます。登録内容を先に確認するには`install --dry-run`を使います。管理者権限やパスワードの保存は不要です。インストーラーは自動更新用ファイルと本人用の定期タスクを作り、Codexの起動・終了や、その場でのプラグイン更新は行いません。

自動更新の動作は次のとおりです。

- CodexとChatGPTのアプリ・CLIがすべて終了していれば、対象marketplaceだけを標準CLIで同期します。モデルを呼ばず、AIのトークンを消費しません。
- 使用中、稼働状態が判別できない場合、別の更新処理が実行中の場合は、更新を見送って次の確認を待ちます。アプリを長時間開いたままの場合は、閉じた後の確認まで更新されません。
- 通信・認証・CLIエラーでは、削除や再インストールを試みません。更新失敗は短い診断として残り、次の定期確認で再度確認します。利用者のアプリを停止・再起動しません。
- 個別の`overrides.json`、親設定、指示、フックの信頼設定を編集しません。フック定義が変わった版では、Codex標準の`/hooks`による再確認が必要な場合があります。

登録状態と直近の結果の確認、定期実行の解除は次のコマンドで行えます。

```text
python scripts/install_updater.py status
python scripts/install_updater.py uninstall
```

checkoutがない場合は、上のPowerShell例と同じ`install_updater.py`のパスに`status`または`uninstall`を渡します。解除後も個別の上書きやCodex設定は残ります。自動更新用のファイルはプラグインの版別キャッシュの外へ保存し、プラグイン更新後は新しい更新処理を読み込みます。固定された起動インターフェースを変更する将来の版では再登録が必要になる場合があります。

診断先は`<CODEX_HOME>/codex-task-routing/updater/state/last-sync.json`です。認証情報やCLI出力全文は保存しません。PCの休止中・ログオフ中には実行せず、次に実行できるタイミングで確認します。通常起動と更新の開始を完全には排他できないため、**毎回の起動直前に必ず最新版になる保証はありません**。

Windowsでは`CODEX_HOME`を約230字以内にしてください。これより長いと、定期実行の起動や更新処理の確認の子プロセスを開始できず、自動更新が止まるか、プラグイン更新後も直前の更新処理のまま動くことがあります。

Windows以外では定期実行の登録に未対応です。Codexが終了している時に`python plugins/codex-task-routing/scripts/updater.py`で同じ一回分の更新処理を実行できます。

## 更新・同じ版の再現・解除

同じ版を再現する場合、両PCの取得元のrefを同じcommit SHAまたは公開タグにそろえ、同じ上書きを用意します。診断のpolicy hashも比較します。モデルの生成結果まで同一になるという意味ではありません。

自動更新を登録せず手動更新する場合は、Codexアプリ・CLIをすべて終了してから次を実行します。`upgrade`だけで導入済みプラグインも更新します。

```text
codex plugin marketplace upgrade codex-task-routing
```

異なる版へ切り替えるときは、既存の登録元を確認し、このプラグインを解除してから対象marketplaceの登録を外し、Git refを明示して登録し直します。次の `COMMIT_SHA_OR_TAG` を取得したい版に置き換えます。

```text
codex plugin remove codex-task-routing@codex-task-routing
codex plugin marketplace remove codex-task-routing
codex plugin marketplace add acecore-systems/codex-task-routing --ref COMMIT_SHA_OR_TAG
codex plugin add codex-task-routing@codex-task-routing
```

配布ファイルを変更するPRではmanifestの版も上げます。CIはSemVerとして不正な版、同じ版、後退、build metadataだけの変更を拒否します。ローカルの開発でもmanifestの版を上げて再導入します。更新は旧導入コピーを削除するため、作業中のフックから自己更新しません。起動後の新しいタスクで適用を確認します。

プラグインを解除する場合は、先に`install_updater.py uninstall`で定期実行を解除し、次を実行します。通常のCodex設定や上書きファイルは残ります。

```text
codex plugin remove codex-task-routing@codex-task-routing
```

## トラブル対応

- **新しいタスクに方針が表示されない:** `/hooks` でフックの存在と信頼状態を確認します。スキルの検出だけでは常時適用されません。フック定義が変わった後は再確認が必要な場合があります。
- **`status --json` がエラーを返す:** `error_code` で原因の種類を、`hint` で秘密値を含まない対処を確認します。既存の `error` フィールドも互換性のため維持します。設定ファイルを自動修正する診断ではありません。各コードの意味は[設定の詳細](plugins/codex-task-routing/skills/task-routing/references/configuration.md)を参照してください。
- **既存の分担指示との競合が表示される:** Codexホームや作業先の `AGENTS.md` / `AGENTS.override.md` に既知の分担指示があると、二重適用を防ぐためフックは新方針を追加しません。旧指示を自動削除しないため、バックアップして対象の分担節だけ移行し、新しいタスクで適用を確認します。異なる表現の類似方針をすべて機械検出できるわけではありません。
- **要求したモデルやeffortで動いたか不明:** `status` の要求値だけでは実行を証明できません。子の起動時のツールと実行結果を確認します。モデルとeffortの利用可否はホストごとに異なります。
- **通常Chat経路が有効にならない:** `chatgpt.json`、新しいタスクのフック表示、ログイン済みBrowser、UIの `Pro` 表示を順に確認します。上限・モデル変更・許可不明の場合は送信しません。
- **自動更新されない:** `install_updater.py status` と `last-sync.json` を確認します。CodexかChatGPTが稼働中、稼働状態が不明、別更新が実行中、PCが休止・ログオフ中の場合は更新を見送ります。
- **別の版へ戻す、または固定する:** 実行中のCodexアプリ・CLIを終了し、上の再登録手順でcommit SHAまたは公開タグを指定します。上書きは自動削除されません。
- **同モデル委譲ガードが効かない、または拒否された:** まず`<CODEX_HOME>/codex-task-routing/spawn-guard.json`の`mode`を確認します。ファイルがない、不正、未知の`mode`、安全でないパスは`off`として扱われます。次に、更新後に`/hooks`で再信頼したか、新しいタスクから切り替えたかを確認します。`spawn_guard.py status --json`の`by_verdict`、`by_reason`、`would_deny`で集計を見て、判定不能の理由（`model_omitted`、`role_may_override_model`など）が多い場合は、判定できない委譲が多いことを示します。

## 開発と検証

開発用のBrowserスクリプト模擬テストにはNode.js 22を使用します。

```text
python -m unittest discover -s tests -v
python scripts/check_package.py
node --test tests/test_chat_browser.cjs tests/test_chat_browser_run.cjs
```

テストでは既定本文の再現、上書き・無効設定、開始イベント、既存指示との競合、ファイル保護を検証します。実際のフック信頼操作・通常会話の分担判断・他OSは別の確認対象です。検証実績は [docs/validation.md](docs/validation.md)、版ごとの変更は [docs/changelog.md](docs/changelog.md) を参照してください。

詳細は必要な節だけ読みます。方針の正本は `defaults/config.json`、分類と引継ぎのテンプレートは `defaults/templates/` にあります。既定の元文書は個人パスと適用経路を調整し、意味を保った正本スナップショットと照合しています。

このプラグインは認証・会話履歴・メモリ・契約の利用率を読みません。方針フックはローカルの有効方針キャッシュを書き出し、観測台帳は読み取るだけです。親の助言フックは上記のハッシュ化した識別子と固定状態を専用SQLiteへ保存します。観測CLIはAIから渡された短い構造化データを専用台帳へ保存します。任意の自動更新機能は専用ファイル・設定・同期診断とWindowsの定期タスクを作成し、標準CLIによるmarketplace更新を行います。任意の同モデル委譲ガードは既定でオフで、オフの間は何も出力せず、何も記録しません。有効化した場合だけ、モデル名や本文を含まない判定結果を専用ファイルへ記録し、拒否は同モデルと確定した`enforce`のときだけ行います。このフックは認証・会話履歴・メモリを読まず、モデル・ネットワーク・子の起動を行いません。内部ログ解析や常時計測は含みません。

MIT License。詳細は [plugins/codex-task-routing/LICENSE](plugins/codex-task-routing/LICENSE) を参照してください。
