# 通常作業の観測

使うのは利用者が明示開始した有限campaignだけです。通常作業の全範囲実測3件を目標とし最大6試行します。欠測は試行に数えるが成功に数えません。指示の修正・監査・導入確認・短い質問は対象外です。campaign未開始・終了後や版更新時に新規記録を自動開始せず、モデル・子の起動やテストを計測のために追加しません。旧台帳と記録は保持します。

## 明示開始

ユーザーが対象・範囲を明示した場合のみ、一度campaignを開きます。設定作業自体は観測対象にせず、3件の実測に達した後または6試行後は自動再開しません。次のcampaignも別の明示指示が必要です。IDは英数字、`_`、`-`の64文字以内。途中で終了する場合は進行中の記録を完了または中断として閉じ、`observation campaign-close --campaign-id <campaign-id> --sampling-key <key> --reason <reason>` で理由付き停止できます。記録は削除されません。

```text
python <routing.py> observation campaign-open --campaign-id <campaign-id> --purpose <purpose>
```

## 開始と再開

フックが示す `Plugin root` の下の `scripts/routing.py` を使います。下記の `<routing.py>` をそのルートと連結した絶対パスに置き換え、パスはシェルに合わせて引用してください。

```text
python <routing.py> observation start --campaign-id <campaign-id> --task-id <root-thread:work-unit> --kind normal --json
```

task_idには親threadと作業開始turnの組など、同じ仕事の間は変えない値を使います。ホストがIDを返さない場合は衝突しない短い識別子を選び、返された記録とともに引継ぎます。IDを探すために全履歴を読みません。startが返したsampling_keyもtask_idと一組で保持します。再開・子・孫は別の枠を予約せず、保存したキーでstatus/completeを使います。方針が変わっても元のキーでその作業を閉じます。

`start` の結果が上限・対象外・状態不明なら新規記録を作らず、元の仕事を続けます。新しい子には既知のtask_idと要求model/effort・理由、記録の返却先を普段の引継ぎに添えます。

## 最終報告の前

親は既存の検証結果と子の返却情報を一組にまとめ、同じtask_idへ渡します。

```text
python <routing.py> observation complete --campaign-id <campaign-id> --sampling-key <start-returned-key> --task-id <root-thread:work-unit> --input <result.json>
```

入力ファイルは作業先のローカル一時領域へ置き、会話本文・認証情報・原本・全ログを入れません。台帳は `<CODEX_HOME>/codex-task-routing/observations/<sampling_key>.json` に保存されます。プラグインの版別キャッシュやGitリポジトリの配布ファイルへ書きません。

取得できた実行情報を `runs` に残し、要求設定と実際に観測した設定を分けます。観測値には実行結果の参照が必要です。初回起動と再開を別runとし、親・直接子・孫の所属を残します。子の自己申告だけで実行設定を確定しません。検証は既存結果を参照し、手直しは短く記載します。

全体の使用量を取得できない場合も記録を閉じます。次は実行情報も検証参照も取得できなかった場合の最小例です。取得済みの結果がある通常作業でこの例へ機械的に置き換えないでください。

```json
{
  "state": "completed",
  "scope": "対象作業全体の結果を記録。使用量は親子とも未取得。",
  "measurement": {"state": "missing", "reason": "実行情報を取得できない"},
  "verification": {"reference": null, "reason": "既存の検証参照を取得できない"},
  "rework": "不明",
  "runs": []
}
```

進行中の親turnは最終使用量が未確定なので、欠測理由を付けて残します。記録完了と実測の充足は別です。後の同じタスクの通常の再開時に既存の完了結果を得られる場合だけ同じ記録を補い、記録のために追加のタスク・子・ツール修復を始めません。

`scope` は500文字以内で対象作業と記録範囲を示します。終盤だけの結果ならその旨を残し、別作業や全工程の測定と混ぜません。

親と子を含む[入力例](observation-example.json)もあります。値と参照先は書式説明用の架空データです。実作業では取得済みの根拠に置き換え、不明値は理由付きnullにします。`measurement.state` は、記録対象に親が含まれ、全runが完了し全カウンターの全turn範囲を確認できた場合だけ `measured`。一部取得済みなら `partial`、全量不明なら `missing` です。

再開時の入力は同じ作業の全記録です。次で自分の記録だけを読み、以前の `runs` と取得済み値・検証参照を残して追記します。別のturnには別の `run_key` を付け、以前の完了runを進行中へ戻しません。

```text
python <routing.py> observation status --campaign-id <campaign-id> --sampling-key <start-returned-key> --task-id <root-thread:work-unit> --json
```

返却された `record` から `state`、`scope`、`measurement`、`verification`、`rework`、`runs` の6項目を入力ファイルへ取り出して更新します。台帳全体や会話履歴を読み直す必要はありません。CLIは台帳全体の属性をcompletion入力として受理しません。

## 使用量の範囲と解釈

- 完了turnの累積差分は、完了時の累積値から最初の応答より前の累積値を引きます。最初の応答後の累積値を基準にすると、その応答の利用分が抜けます。途中区間は `partial_turn`、完了範囲を確認できた場合だけ `full_turn` とします。
- 対象区間の欠測・reset・カウンター不整合や範囲不明があれば全量を確定しません。既存の検証済み取得結果を使い、このプラグインへログ全文を渡さないでください。
- `cache_input_tokens` は `input_tokens` の内数、`reasoning_tokens` は `output_tokens` の内数です。二重に足しません。親子の対象範囲が未確認なら総量を合算せず、同じ条件の比較基準がなければ節約率は不明です。
- API料金とCodexのアカウント共通使用率を直接換算しません。操作件数だけで無駄とは判断せず、理由なく同じ検証を繰り返した証拠や手直しを確認します。

## 状態確認

```text
python <routing.py> observation status --json
```

現在の方針の残枠・進行中・記録完了・使用量の欠測を区別します。過去の群を読む場合は保存した `--sampling-key` を指定します。campaignの目標達成または試行上限後は新規記録を増やさず、既存記録の通常継続だけを認めます。旧台帳はそのまま残り、旧記録を読む・補完する場合は `--campaign-id` を省きます。破損や権限不足は不明として残し、空の台帳で上書きしません。観測の問題だけを理由に本来の仕事を止めないでください。

## 0.8.0の比較

子のeffortは固定したまま、親変更とChat適合工程の移動を分けて比較します。モデル別の割合ではなく親子合計の使用量と同じ合格条件までの手戻りを見ます。Chatのメッセージ・人の修正・初回受入はCodexトークンと混ぜず、必要な短い比較記録を別途使います。旧台帳のキーや記録を変更せず、比較のためだけに追加仕事を起動しません。

## 根拠付きの前後比較

[比較入力例](observation-comparison-example.json)は架空の説明用データです。基準側と変更側の観測completionを各 `completion` に入れ、各記録の別々の証拠参照を `completion_ref` に指定します。`conditions` の仕事、合格基準、環境、実際の親model・effortはそれぞれ値と根拠を記録し、前後で一致する必要があります。入力値は実測を証明するものではないため、根拠の妥当性と、同じ仕事・難度・成果物が比較可能かは利用者が確認します。モデル構成や実行環境が異なる場合、差を方針だけの効果として扱えません。

各側の `coverage` は対象仕事の全工程を収録できた証拠、`independent` は親子・再開runに重複計上がない証拠、`quality` はその側が同じ合格条件を満たした証拠です。`independent_comparisons` は前後の計測区間が重複しない証拠です。確認できた場合だけ `{"value": true, "evidence_ref": "根拠", "reason": null}` とし、不明や未達は `value` に null または false、`evidence_ref` に null、`reason` に理由を記載します。条件の不明値も同様に理由付きnullとします。Chat件数は別欄で、取れなければ理由付きnullとします。観測の `rework`、検証参照と品質を別に確認してください。

```text
python <plugin-root>/scripts/compare_observations.py --input <comparison.json>
```

CLIは指定ファイルを読むだけで、既存台帳や観測枠を変えません。親と親子合計の入力+出力tokenを別々に比較し、cacheは入力の内数、reasoningは出力の内数として二重に足しません。基準ゼロ、部分・欠測、既知のrun IDの重複、比較条件・品質・範囲・非重複の根拠不足では削減率を理由付きnullにします。親が減っても総量が増えることがあり、品質・手戻り・Chatは別途評価します。結果はtoken差であり、料金やアカウント共通枠の節約の証明ではありません。

比較CLIは使用量を自動取得せず、会話履歴を探索しません。現ホストで親子別の実token値を確認できない場合は、利用者またはホストが提供する対象区間の根拠を待ち、不明のまま扱います。アカウント全体の使用率を個別作業のtoken数の代わりに使いません。計測や比較だけの新しいタスクを起動せず、対象外の調査記録を無理に基準としません。

`run_id` は同じ実行やturnを識別する値です。thread IDしか取得できない場合に実行IDを推測・捏造せず、既存スキーマの `run_id: null` と `run_id_reason` を使い、範囲の独立性を別の根拠で確認します。既知IDが重複する場合、たとえ範囲の独立性を申告していても比較率は不明です。

## 明示登録したthreadの集計値を取得する

これは本文・会話履歴を読む機能ではありません。まずユーザーが有限campaignを明示し、その中で対象の通常作業を予約します。計測対象と親子関係が確定してから、[架空の登録例](usage-enrollment-example.json)のroot・子のUUIDと親・depthを、本人が承認した対象だけに置き換えてローカルの一時ファイルに記入します。thread IDとrun/turn IDを混同せず、既知の子はrootからの深さと直接親を照合します。追加登録は既存IDを保持し、未登録IDのデータは読みません。

```text
python <routing.py> observation enroll --campaign-id <campaign-id> --sampling-key <key> --task-id <task-id> --input <enrollment.json>
python <plugin-root>/scripts/usage_snapshot.py --codex-home <codex-home> --db <codex-home>/state_5.sqlite --campaign-id <campaign-id> --sampling-key <key> --task-id <task-id> --thread-id <enrolled-id>
```

スナップショットはそのthreadの累積合計だけを読みます。`mode=ro`、固定SQL、登録IDに絞り、`id`・`source`・`tokens_used`・`model`・`reasoning_effort`・`cli_version`の6列のみ取得します。sourceは親IDとdepthを照合し、他のsource属性を返しません。本文・履歴・タイトル・rollout path・全thread一覧を取得せず、DBファイルの識別値とschemaの変更を検出します。現在の対応は検証済み `state_5.sqlite` の `schema_version=123`・`user_version=0`、threadの `cli_version` が `0.165.0-alpha.1.2`、root source `vscode`、子の `thread_spawn` 形式に限ります。異なる版・schema・source・欠測・権限問題は不明として停止します。

各threadにつき、**対象作業の最初のモデル処理前**と**その対象処理が終了し累積値が確定した後**の両方でsnapshotを取り、結果だけを安全な作業先の一時ファイルに保存します。開始前・終了後の境界と、その間のresetがなかったことを外部の実証可能な根拠で確認できる場合だけ、次の3根拠を渡します。証拠が欠けたときは該当引数を省き、結果は `partial_turn` です。差分が非負なだけで途中resetなしとは断定できません。

```text
python <plugin-root>/scripts/usage_interval.py --before <before.json> --after <after.json> --evidence-ref <snapshot-evidence> --start-boundary-ref <start-evidence> --end-boundary-ref <end-evidence> --continuity-ref <no-reset-evidence>
```

同じthread、親関係、設定、DB識別値、schema/版、時刻順序を照合し、counter減少や不一致を拒否します。結果の `usage` を対応するrunに入れて既存completionを更新します。全runの全範囲、親子の漏れ・重複がなく品質条件を満たす場合のみ比較対象になります。利用中の親turnの途中、起動済みの子の初回処理後、終了直後にまだ最終値の確定を確認できない場合はpartial/欠測にし、0や完了値を推測しません。親と子の開始前後の境界を取れない場合は実証済みの第三者・ホスト機能を待ちます。配布hookの登録・信頼と実際の境界はホストで別途確認し、境界の確実性を推測しません。

この方式のbasisは `aggregate_total_tokens` で、input・cached input・output・reasoningの内訳はありません。4counter形式と混在して比較しません。modelとeffortはthread設定のsnapshotであり実行backendの証明ではなく、比較結果は `runtime_model_verified: false` です。同じ設定のtoken差として解釈します。取得値が不十分な場合や開始前根拠が不明な場合は理由付きnullのままとし、アカウント共通利用率やAPI価格へ換算しません。

実装根拠は、[stateの集計値抽出](https://github.com/openai/codex/blob/23934d31b15b1127c83535f84854e5009f7cd51d/codex-rs/state/src/extract.rs)、[thread metadataへの反映](https://github.com/openai/codex/blob/23934d31b15b1127c83535f84854e5009f7cd51d/codex-rs/thread-store/src/thread_metadata_sync.rs)、[子の利用量を分けた集計](https://github.com/openai/codex/blob/23934d31b15b1127c83535f84854e5009f7cd51d/codex-rs/ext/goal/src/accounting.rs)です。将来版への互換性は推測しません。

## 次の通常依頼を事前にarmする

明示campaignを開いた後、現在の設定turnのrecordを作らず、指定rootの**次の依頼**に一回だけ備えることができます。外部CLIから、架空の[登録例](usage-enrollment-example.json)を対象root UUIDに置き換え、未知の子を列挙せず `start_evidence_ref` はnullとします。子の自動登録を今回許可する場合だけ `allow_descendants` をtrueにします。対象外の子や未確認のparent/depthはpartialです。hookの有効・信頼状態を実際のホストで確認してからarmしてください。

```text
python <routing.py> observation arm-next --campaign-id <campaign-id> --sampling-key <key> --task-id <unique-task-id> --input <enrollment.json>
```

この時点ではtask recordを作りません。次の依頼はユーザーが通常どおり送ります。指定rootの次のUserPromptSubmitで一度だけ暫定recordを作り、prompt本文を保存せず、モデル処理前にrootの累積baselineを取得します。読み取れない入力はarmを解除し、baseline失敗は欠測として残し、別の後続依頼にarmを使い回しません。statusの `arm_failure` とcampaignのattemptを確認してください。hookが実際に動かなかった場合は登録・信頼を確認し、勝手に後続へ回さずcampaignを明示停止してください。

依頼がまとまった通常作業だと判断できた場合だけ、同じIDを確認します。短い質問・監査・設定作業は `confirm-normal` を行わず、理由を付けた `interrupted` completionで対象外として閉じます。暫定予約が消費した試行は上限に数え、測定成功には数えません。

```text
python <routing.py> observation confirm-normal --campaign-id <campaign-id> --sampling-key <key> --task-id <unique-task-id> --evidence-ref <normal-work-evidence>
```

## hook集計とStop後の照合

有効な `PreModelRequest`/`PostModelRequest`、`Stop`、`SubagentStart`/`SubagentStop`の観測専用hookは、登録されたrootと許可された子だけを処理します。親子の要求ごとに4counterを対応させ、prompt・assistant本文・transcript path・error本文は記録せず、モデルを起動しません。子はSubagentStartでsourceの親・depthを照合し、その時点の累積値が0のときだけ開始値に使います。登録前に子が処理を始めた場合、DB未反映、未知版、親不一致、子の開始/終了欠落、対応不能な並列pre/post、失敗、上限超過はpartialです。request IDがないため曖昧な対応を推測しません。

root Stopでは対象区間を固定し、その場で登録された親子それぞれのDB累積値を最大3回、1秒以上間隔を空けて読みます。安定した2回の値が一致し、各threadの終了値−baselineがhookのinput+output合計と一致した場合に `matched` として保存します。遅延・不一致・読取不能・未完のペアはpartialです。後続turnでDBが増えた値を前の仕事へ混ぜません。外部CLIからStop後かつ次のモデル処理前に `capture-verify` を1秒以上空けて2回実行する代替もあります。初回のpendingや不一致では記録を更新せず、許された回数内で安定を確認します。次turn開始後に再読取して前の仕事の値と扱わないでください。

```text
python <routing.py> observation capture-verify --campaign-id <campaign-id> --sampling-key <key> --task-id <unique-task-id> --db <codex-home>/state_5.sqlite --continuity-ref <no-reset-evidence>
```

2回の一致は長時間遅延した後の書込みや途中resetの不存在を論理的に保証しません。CLI・DB schema・実行条件の変更、カウンターresetの有無、hookが依頼前から対象範囲の終わりまで信頼・有効だったことは独立の根拠を確認します。Stopはその応答の終了であり、本人承認待ちや途中確認なら仕事全体の完了ではありません。対象全工程の完了と品質を満たさない場合はfullにしません。登録状態や2回の一致だけを根拠に、未確認の条件を推定しないでください。

```text
python <routing.py> observation capture-certify --campaign-id <campaign-id> --sampling-key <key> --task-id <unique-task-id> --continuity-ref <no-reset-and-continuity-evidence>
python <routing.py> observation capture-export --campaign-id <campaign-id> --sampling-key <key> --task-id <unique-task-id> --evidence-ref <capture-evidence> --coverage-ref <hook-and-scope-evidence> --work-completed-ref <work-completion-evidence>
```

`capture-certify` はStop時に固定したmatched結果のみを使い、DBを再読取しません。続けて `capture-export` から各runを取り出し、親子の対応・比較条件・テスト結果を確認したcompletion JSONを `observation complete --campaign-id <campaign-id> --sampling-key <key> --task-id <unique-task-id> --input <completion.json>` に渡します。hook basisでmeasuredを確定するときは、保存された照合済みrun・counterとの一致も検査します。`capture-export` は、上記の連続性、hook有効期間・親子と全requestの範囲、対象仕事と品質の完了について実際の証拠があり、すべての照合が成立する場合だけfullを出します。文字列は証拠への参照であり、CLIはその真偽を自動検証しません。不明なら参照を付けずpartialとし、必要に応じ `interrupted`/欠測で記録を閉じます。出力の各 `usage` は `hook_request_counters` basisとして、識別済みのthread IDとturn IDの組を `run_id` に、親子関係と共にcompletionへ記録し、他basisと混ぜません。設定model/effortとbackend実行値を区別します。

各root/子の `run_id` は `thread_id:turn_id` という二つの実IDから導いた識別子です。実際の単独run UUIDと誤認しません。既存recordを更新する場合も、以前のrunや既知の値を保持します。比較には両側の同じ仕事・合格条件・設定・環境、親子の範囲と品質をそれぞれ確認して [比較入力例](observation-comparison-example.json) に渡します。比較に足る従来基準がない場合は削減率を不明にします。

新しいhookの信頼状態は実際のホストが要求・表示するものを確認し、設定ファイルを自動編集しません。合成テストとhooks/listの認識だけで、稼働中ホストの信頼や本番の親子全量取得を成功扱いしません。
