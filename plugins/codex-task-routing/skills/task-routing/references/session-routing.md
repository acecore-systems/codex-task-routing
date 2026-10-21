# セッションとCodexクラウドの計画

model・effortと、仕事を置く実行環境は別に選びます。短い仕事、適任担当の継続、Codex側の現物への反復アクセスは現在の担当に残します。別目的・独立成果物・別repo/PR・継続管理の利点が引継ぎ・受入の負担を上回る場合だけ別セッションを提案します。長文脈、利用枠回避、同モデルへの移管だけでは分割しません。

この版の`session_plan.py`はPython標準ライブラリによるローカル計画だけです。ネットワーク、モデル、CLI、ブラウザー、タスクを起動せず、認証・会話履歴・個人メモリ・稼働Codexホームも読みません。入力JSON以外を探索せず、状態ファイルを更新しません。`execution_authorized`は常にfalse、クラウド出力は`planning_only:true`、`runtime_implemented:false`です。`preflight_complete:true`も、供給された観測の機械照合が通ったという意味です。実接続・送信・テスト・ホストの許可の証明ではありません。実行経路は現行Cloud UIの`codex_cloud_ui`と明示handoffだけです。`runtime_implemented:false`はplanner自身が非実行という既存契約で、UIの利用不能を意味しません。旧Cloud CLIの実行コード・入力受付・専用資料は配布しません。実UIと実テストの確認範囲は、リポジトリの版別検証記録を参照します。

## 候補の提案

```powershell
python <plugin-root>/scripts/session_plan.py session --facts <session-facts.json>
```

入力例は架空の独立タスクです。`null`は未確認で、falseや不存在を意味しません。`existing_owner:null`も、既存担当がいないと確認したことにはしません。

```json
{
  "schema_version": 1,
  "candidate": "codex_cloud",
  "current_owner_suitable": false,
  "existing_owner": null,
  "independent_work_unit": true,
  "handoff_benefit_exceeds_cost": true,
  "reasons": ["separate_repo_or_pr", "independent_deliverable"]
}
```

候補は`continue_current`、`subagent`、`existing_local_session`、`new_local_session`、`codex_cloud`、`chat_pro`です。提案はモデルを選び直しません。`existing_owner`を記録する場合は`owner_id`、その担当の`route`、`suitable`（true/false/null）をそろえます。適合する現在担当は継続を最優先し、それ以外の適合する既存担当は`reuse_existing_owner`へ進みます。現在担当とは今この完了単位を所有している担当、existing_ownerは他の既存セッション等の再利用候補です。適合しないと確認済みの既存ローカルセッションは`choose_another_context`、未確認なら候補と不足情報を返して`check_owner_reuse`へ進みます。現在の担当が適任なら継続します。候補が通っても作成・送信は行いません。

ホストの実行条件は各操作で確認します。現ホストの`create_thread`は本人が明示的に新タスクを依頼した場合だけ使い、create_threadのtarget.environment.type=worktree選択も本人の明示指定が必要です。採用済み提案や元依頼で同じ範囲が明確に承認済みなら再確認しません。単なる候補提案を実行承認へ読み替えません。別トップレベルセッションのmodelは本人の明示指定がなければ省略し、thinkingも既定を維持します。標準子の自動model/effort選定とは別です。通常のrepo作業で既存権限に基づく専用worktreeを作る運用は、このcreate_thread選択条件の対象外です。`send_message_to_thread`も本人の送信指示が必要で、別タスクからの報告要求だけでは足りません。

標準子は既存の役割・depth・並列/直列条件を適用します。通常Chat Proは[Chat方針](chatgpt.md)と[承認](chat-authorization.md)をそのまま適用し、このhelperはChatの許可を付与しません。クラウド/別CLI/新タスク経由で子やホストの制約を迂回しません。

## 現行Cloud UIの標準手順

`session_plan.py`の`transport: codex_cloud_ui`で、現在のホストで実際に提供されるネイティブCloudの画面操作を計画します。現在のUIで選ばれた実行場所・公開環境・元タスクを観測し、利用可能な正式ツールで明示送信・同一task回収を行います。画面のURLやtask UUIDからbackendを推測しません。

元依頼で許可済みの同じ対象・材料・操作・環境には承認を再利用し、独自の追加承認フローを作りません。元の承認に含まれない材料転送・新しい対象や、実ホストが要求する確認はその境界を守ります。通常Chatのopt-inだけをCloudの許可へ転用せず、ネイティブCloudの操作を通常Chat Proへの依頼として扱いません。通常Chat/Work/モデルAPIへのfallbackで実行場所や制約を迂回しません。

1. 同じ所有者がinspectとdispatch-planで、依頼ID/hash・範囲・返却先、対象環境と利用できる検証手段を確認します。既存タスクや送信結果不明の記録があれば元taskの回収を先に行い、新規送信しません。
2. 実送信の前に、同じrequest IDの排他的な永続claimを`cloud_record.ClaimStore`でローカルへ保存し、unknown_outcomeを残します。承認済みの実promptと材料hash、観測した元タブ/画面、対象環境を保持して、一度だけ送ります。送信後は実際の元タブ/task IDと到達点を同じ記録に保存します。このhelperはUIを操作せず、本人承認や実送信を証明しません。UI所有者は下記のlock契約を守って保存と正式なUI操作を対応付け、claim不明・不一致では送信を止めます。
3. `start_skill`の自動注入に依存せず、下記の開始手順を依頼本文に明記します。まず指定した絶対cwdでrepo/branch/期待revisionを照合し、README・AGENTSとcloud-environment runtimeスキルを読み、network policy・実行可能な検証方法を確認してから、許可操作だけを行います。必須検証と停止条件も本文へ渡します。ツールやスキルが未提供なら不足を返し、利用できると推測しません。認証確認が必要でも秘密値を返さず、復元artifactのsource revision宣言と実checkoutで観測したrepo/base revisionを区別します。
4. 同じ元タブ/taskで状態と成果物を回収し、receiveで元ID/hashを照合してから所有者が内容を受け入れます。回収は新規送信の承認・生成枠を要求し直しません。実行したcheck、環境、結果、失敗・skip・未検証を保持し、環境の公開・タスク開始・復元成功をテスト成功と扱いません。

回収エラーはUI taskの不存在・認証失敗・未送信の証明ではありません。元UIの記録・同一taskへ戻り、再送や新タスク化をしません。所有者の交代や回答待ちでも再送しません。HTTP listenerはCloud UIの利用要件ではなく、自動注入とともに通常運用の残対応にはしません。listenerが必要な個別工程でだけ実能力を確認し、ソケットを使わないunitテストの成功で代替しません。

依頼本文には、具体値を埋めて次の開始手順を含めます。hash用JSONだけに閉じず、Cloud担当が読む本文にも渡します。

```text
開始cwd: <absolute-cloud-checkout>
対象: <repository> / <branch> / 期待revision <full-sha>
最初にpwdとgit status/branch/HEADを確認し、不一致なら編集せず不足を返す。
READMEとAGENTSを読み、cloud-environment runtimeスキルとnetwork policyを確認する。
復元artifactを使う場合はsource revision/hashと実checkoutの観測を分けて報告する。
許可操作: <approved-operations>。終端: <agreed-endpoint>。
必須検証: <commands-and-acceptance-criteria>。
ツール・スキル・権限・検証環境が不足する場合は未検証の理由を返す。
秘密値・Cookie・OAuth JSON・認証画面を返さず、承認範囲を越えない。
```

自動注入の成否を直すために同じ依頼を作り直さず、現在のタスクと本文の開始手順を使います。HTTP listenerを使わない工程に待受検証を追加しません。

## クラウド引継ぎの固定範囲

rootが実行範囲を評価します。子は提案と不足理由をrootへ返します。通常Chat opt-inをクラウドへ流用しません。クラウド側のモデル/effortがローカルと同じになるとも仮定しません。秘密値を扱う認証工程は[ローカル認証](local-auth.md)へ分け、子は認証を始めません。

```json
{
  "schema_version": 1,
  "request_id": "example-cloud-unit-1",
  "input_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "owner_id": "root-example",
  "return_to": "root-example",
  "repository": "example/project",
  "branch": "codex/example",
  "expected_revision": "1111111111111111111111111111111111111111",
  "environment_id": "example-environment",
  "endpoint": "指定範囲の実装と検証を返す",
  "acceptance_criteria": ["指定した回帰が解消し、検証証拠がある"],
  "required_checks": ["python -m unittest discover -s tests -v"],
  "allowed_operations": ["read", "edit", "test"],
  "state": {"submission": "not_sent", "task_id": null}
}
```

ID、hash、revisionは架空の例です。実依頼では承認済み材料のhashと実revisionを使います。`input_sha256`は材料の識別用で、材料の読取り/承認をhelperが行うわけではありません。`handoff_sha256`は`state`を除く正規化JSONのSHA-256です。helper出力の値を同じ依頼のlive/resultへ渡します。hashやowner宣言は署名・本人認証・権限証明ではありません。repo/branch/期待revision/environment/終端/許可操作が変わると別scopeです。

`allowed_operations`はread/edit/test/commit/push/create_draft_prに限定します。許可リストは今回の本人承認やツールの実在を証明しません。merge/deployはこのhelperの範囲外です。必要な上位判断はrootへ返します。秘密値、OAuth JSON、Cookie、個人画面、会話履歴を入力しません。自由記述は短い検証条件・既存証拠への参照だけにし、helperを秘密検出器と扱いません。

## inspect / dispatch-plan / receive / accept

```powershell
python <plugin-root>/scripts/session_plan.py cloud --handoff <handoff.json> --phase inspect
python <plugin-root>/scripts/session_plan.py cloud --handoff <handoff.json> --phase dispatch-plan --live <live.json>
python <plugin-root>/scripts/session_plan.py cloud --handoff <handoff.json> --phase receive --live <live.json>
python <plugin-root>/scripts/session_plan.py cloud --handoff <handoff.json> --phase accept --result <result.json> --acceptance <acceptance.json>
```

- `inspect`: 元taskまたはenvironmentを調べるためのホスト機能/読取り範囲を照合します。liveがなければ不足情報を返し、送信しません。
- `dispatch-plan`: 新規送信の準備条件を照合します。全部通っても`review_actual_host_dispatch_contract`で止まり、送信可能・許可済みと宣言しません。実ホスト条件の確認と現行UIでの明示送信は別工程です。
- `receive`: 元task IDの回収条件だけを照合します。dispatchの承認・材料送信・利用枠を要求し直しません。読取りにも実ホストの許可と同じ対象の既存承認が必要です。
- `accept`: 元ID/hash、実repo/base revision、変更成果物、必須検証、所有者の意味確認を照合します。完了・失敗・部分・未検証を区別し、実行や状態更新は行いません。

`state.submission`はnot_sent/sent/unknown_outcome/collected/acceptedです。sent/collected/acceptedには元task IDが必要です。timeout等で送信結果が分からなければunknown_outcomeへ進め、task IDが不明でも再送しません。dispatch-planはnot_sent以外から新規送信へ進まず、既存taskの回収・照合・受入を返します。引継ぎや所有者交代も再送理由にはしません。helperは永続台帳を持たないため、所有者は元の依頼記録で状態を保持し、not_sentへ戻さない責任があります。UI所有者は同じprivate保存先/request IDで送信前claimを保存し、plannerの宣言変更や保存先変更で再送を迂回しません。

## live観測の形式

`--live`は次のフィールドだけを受け付けます。観測はUTC offset付きISO時刻で、照合時から5分以内・未来でないものを使います。既存承認の参照を再利用し、承認を新規取得するためだけの試験書込みを行いません。

| フィールド | 値 |
| --- | --- |
| schema_version | 1 |
| handoff_sha256 | 元handoffの不変範囲のhash |
| task_id | stateの元task IDと同じ値。未送信ならnull |
| surface | codex_cloud。chatgpt_work/normal_chat/unknownは未準備 |
| transport | codex_cloud_uiだけを実行経路として受理。unknownは未準備、他の値は入力エラー |
| actor | role=root、depth=0、executor_id=owner_id。宣言は実権限の証明ではない |
| observed_at | timezone付きISO時刻 |
| checks | 操作別の観測オブジェクト |

checksの各項目は`{"state":"verified","reference":"既存の同一範囲の証拠参照"}`の形です。状態はunknown/verified/permission_checked/blocked/unavailable/confirmation_required。verified/permission_checkedにはreferenceが必要で、helperは参照を取得・検証しません。unknownは未確認であり、拒否や許可へ変換しません。実観測と一致しなければrootへ返します。

| 段階 | 必要なchecks |
| --- | --- |
| 共通 | host_permission |
| inspect | inspect_capability、inspect_authorization |
| dispatch-plan | dispatch_capability、target_authorization、materials_authorization、environment、repository_revision、test_environment |
| receive | receive_capability、receive_authorization |

environment/repository_revision/test_environmentは実観測verifiedが必要で、permission_checkedだけでは通りません。テスト済み成功を送信前に要求するものではなく、対象環境・期待revision・利用可能な検証手段の確認です。それ以外の権限/操作契約はpermission_checkedを使えます。キー省略はunknownです。UIの表示・環境公開だけでdispatch_capabilityの実接続やrepo/revision・検証能力を確認したことにはなりません。実際の元タスクと実行証拠を分けて記録します。target_authorizationは元の本人依頼で許可された同一対象/操作範囲を再利用できます。毎回新しい本人確認を要求するためのフィールドではありません。`host_permission`は実際の現ホスト制約の確認で、JSONへ値を記入するだけでは成立しません。

ChatGPT Workのタスク作成、cloud非対応のhandoff、simulated prototypeのenvironment作成、ローカルworktreeを、専用Codexクラウドの実行・環境確認へ読み替えません。新規cloud taskの環境と既存taskのsaved stateも同一と仮定せず、現在の公式仕様と実接続を確認します。最新資料・今回の実観測と未検証範囲はリポジトリの検証記録へ分けて残します。

## 現行UIでの既存タスク再開と受入

実ホストが許す正式なUI操作を使い、rootの同じ経路所有者が元記録から再開します。helperのpreflight完了だけでは送信せず、フックから自動起動しません。Work、モデルAPI、旧CLIへ切り替えません。

1. 元記録の所有者、request ID/hash、許可範囲、環境、元タブ/task IDと送信状態を確認します。sentまたはunknown_outcomeなら回収を先に行い、新規送信しません。taskを特定できなければ未解決のまま返します。
2. 同じ元タスクのUIで到達点・成果物・実checkoutを確認します。環境の公開、新規workspace、既存taskのsaved state、復元artifactのsource宣言を区別し、対象repo/base revision・必須検証の環境と結果を実証拠から回収します。
3. receiveで同じ元ID/hashを照合し、所有者が実差分、合格条件、必須check、失敗・skip・未検証を意味確認してacceptへ進めます。差分表示だけでテスト実行済みにしません。必要な検証用適用は承認済みの隔離worktreeで行い、最終反映と区別します。
4. 本来のcheckoutへ反映する必要があれば、最新の期待HEAD/status、適用先、許可範囲を確認して別操作で行います。commit/push/PR/merge/deployは各承認と合意終端に従います。元記録にcollected/accepted、証拠、未解決点、再開先を残し、今回開いた完了タブを閉じます。

自動start_skill注入、CloudでのHTTP待受は、この標準手順の通常の残対応ではありません。

## UI用のローカルclaim

`cloud_record.py`はファイル保存だけを行う共通原語です。ブラウザー・CLI・モデル・ネットワークを起動せず、送信・回収・内容受入は同じ所有者が正式なUI操作とplannerで行います。本文・秘密値・生ログは保存しません。

schema 2の記録は`schema_version`、`binding`、`submission`、`task_id`、`event`だけを持ちます。bindingは`request_id`、`input_sha256`、`handoff_sha256`、絶対`cwd`、`repository`、`branch`、`expected_revision`、`environment_id`、`owner_id`、元UIの`tab_id`です。所有者が確認した本文の正確なUTF-8 bytesのhashを`input_sha256`に使い、本文例の開始手順も含めます。storeを作るrequest IDとbinding内のIDは必ず一致させます。hashとIDは同一性の照合用で、権限の証明ではありません。

caller管理の非公開・Git管理外・リンク/reparse pointを含まない保存先を使い、Windows ACLと共有/同期先はホストで確認します。保存先やrequest IDを変えて結果不明を迂回しません。既存の保存記録は自動削除・変換しません。旧schema・未知形式・破損claim/snapshot・claimのないsnapshotは読み取りを拒否し、未送信へ戻さずrootへ返します。

すべてのread/claim/saveを同じ`with store.lock():`の中で行います。固定inodeのOS lockが同時更新を排除し、永久O_EXCL claimがlock解放・crash・timeout後も再送を止めます。

```python
from cloud_record import ClaimStore

store = ClaimStore(private_state_dir, request_id)
with store.lock():
    record = store.read()  # エラーなら停止。未知・旧・破損をNoneにしない。
    if record is None:
        record = store.claim(binding)  # 正式UI送信の前にunknown_outcomeを永続化。
        # この呼出しでclaimを新規作成した所有者だけが一度のUI送信へ進む。
    # 既存recordがあれば元タブ/taskを照合・回収し、新規送信しない。
```

正式UIで一度だけ送信し、実際の元task IDを確認した後は、同じlock内で最新記録を読み直し、`submission="sent"`、`task_id=observed_task_id`、`event="submission_recorded"`へsaveします。元タスクを回収後は`collected`/`collection_recorded`、所有者が実差分・検証を意味確認した後は`accepted`/`owner_review_recorded`へ同じ方法で進めます。保存前に必ず最新の記録とscopeを照合します。

```python
with store.lock():
    latest = store.read()  # 同じclaim/bindingと正式UIの実観測を所有者が照合。
    store.save({**latest, "submission": "sent", "task_id": observed_task_id,
                "event": "submission_recorded"})
```

saveはbinding変更、既知task IDの変更、状態の巻戻しを拒否します。snapshot保存失敗でもclaimは残り、新規送信しません。helperにclaim解除・履歴消去・自動移行・再送コマンドはありません。実際の送信やテスト成功、内容受入をメタデータだけから推定しません。

## result / acceptanceの形式

resultはschema_version、request_id、input_sha256、handoff_sha256、task_id、status、repository、base_revision、change_kind、output_revision、diff_evidence、checks_completed、verification_status、verification_evidence、artifact_referencesをすべて持ちます。

- status: completed/failed/partial/unverified。failed/partial/unverifiedをcompletedへ丸めません。既知の失敗/部分結果は所有者レビューが未完了でも保持し、owner_reviewへ意味確認の到達点を別に返します。
- repository/base_revision: 実際に使用したrepoと開始revision。handoffと照合します。output_revisionだけでは入力checkoutを証明しません。
- change_kind: changed/read_only/no_change。changedは許可されたeditと、baseから変わったoutput_revisionまたはdiff_evidence（未commit差分等への参照）が必要です。read_only/no_changeは明示した状態として扱い、差分参照を持たず、output_revisionはnullかbaseと同じ値にします。変更不要が合格条件を満たすかは所有者が意味確認します。
- checks_completed: 実行した必須検証の名前の配列。verification_statusはpassed/failed/partial/unverified/not_requiredです。必須検証があればpassed、全必須check、空でないverification_evidenceが必要です。必要な検証がなければnot_requiredを使えます。
- diff_evidence/verification_evidence/artifact_references: 秘密値を除いた既存証拠への参照配列。実行コマンド・cwd・環境・結果・時刻・revision/対象差分を必要に応じ記録します。CLI diffだけはテスト実行証拠になりません。

acceptanceは`owner_id`、`decision`（accepted/needs_changes/unverified）、`criteria_checked`（true/false/null）、`reference`（既存の意味確認の参照）です。同じ所有者が合格条件・実差分・実検証を確認し、重要例外はrootへ返します。ID/hashの一致やメタデータの自己申告だけで受入しません。helperの照合成功はsemantic acceptanceやlive testを代替しません。通常修正は同じ担当へ返し、別セッションへ移るだけで未確認を解消した扱いにしません。
