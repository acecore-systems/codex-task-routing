# セッションとCodexクラウドの計画

model・effortと、仕事を置く実行環境は別に選びます。短い仕事、適任担当の継続、Codex側の現物への反復アクセスは現在の担当に残します。別目的・独立成果物・別repo/PR・継続管理の利点が引継ぎ・受入の負担を上回る場合だけ別セッションを提案します。長文脈、利用枠回避、同モデルへの移管だけでは分割しません。

この版の`session_plan.py`はPython標準ライブラリによるローカル計画だけです。ネットワーク、モデル、CLI、ブラウザー、タスクを起動せず、認証・会話履歴・個人メモリ・稼働Codexホームも読みません。入力JSON以外を探索せず、状態ファイルを更新しません。`execution_authorized`は常にfalse、クラウド出力は`planning_only:true`、`runtime_implemented:false`です。`preflight_complete:true`も、供給された観測の機械照合が通ったという意味です。実接続・送信・テスト・ホストの許可の証明ではありません。別入口[cloud_run.py](cloud-transport.md)は明示CLI transportと永続claimを実装しています。runtime_implemented:falseはplanner自身が非実行という既存契約で、配布版全体の未実装を意味しません。実クラウドへのlive pilotは未実行です。

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
- `dispatch-plan`: 新規送信の準備条件を照合します。全部通っても`review_actual_host_dispatch_contract`で止まり、送信可能・許可済みと宣言しません。実ホスト条件の確認と別transportの明示呼出しは別工程です。
- `receive`: 元task IDの回収条件だけを照合します。dispatchの承認・材料送信・利用枠を要求し直しません。読取りにも実ホストの許可と同じ対象の既存承認が必要です。
- `accept`: 元ID/hash、実repo/base revision、変更成果物、必須検証、所有者の意味確認を照合します。完了・失敗・部分・未検証を区別し、実行や状態更新は行いません。

`state.submission`はnot_sent/sent/unknown_outcome/collected/acceptedです。sent/collected/acceptedには元task IDが必要です。timeout等で送信結果が分からなければunknown_outcomeへ進め、task IDが不明でも再送しません。dispatch-planはnot_sent以外から新規送信へ進まず、既存taskの回収・照合・受入を返します。引継ぎや所有者交代も再送理由にはしません。helperは永続台帳を持たないため、所有者は元の依頼記録で状態を保持し、not_sentへ戻さない責任があります。別cloud_run.pyは同じprivate保存先/request IDの永久claimを実装します。plannerの宣言変更や保存先変更で再送を迂回しません。

## live観測の形式

`--live`は次のフィールドだけを受け付けます。観測はUTC offset付きISO時刻で、照合時から5分以内・未来でないものを使います。既存承認の参照を再利用し、承認を新規取得するためだけの試験書込みを行いません。

| フィールド | 値 |
| --- | --- |
| schema_version | 1 |
| handoff_sha256 | 元handoffの不変範囲のhash |
| task_id | stateの元task IDと同じ値。未送信ならnull |
| surface | codex_cloud。chatgpt_work/normal_chat/unknownは未準備 |
| transport | codex_cloud_cliまたはcodex_cloud_ui。unknownは未準備 |
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

environment/repository_revision/test_environmentは実観測verifiedが必要で、permission_checkedだけでは通りません。テスト済み成功を送信前に要求するものではなく、対象環境・期待revision・利用可能な検証手段の確認です。それ以外の権限/操作契約はpermission_checkedを使えます。キー省略はunknownです。CLIのインストール/help表示は利用可能な操作の調査に使えますが、dispatch_capabilityの実接続や環境・repo/revision・検証能力を確認したことにはなりません。target_authorizationは元の本人依頼で許可された同一対象/操作範囲を再利用できます。毎回新しい本人確認を要求するためのフィールドではありません。`host_permission`は実際の現ホスト制約の確認で、JSONへ値を記入するだけでは成立しません。

ChatGPT Workのタスク作成、cloud非対応のhandoff、simulated prototypeのenvironment作成、ローカルworktreeを、専用Codexクラウドの実行・環境確認へ読み替えません。新規cloud taskの環境と既存taskのsaved stateも同一と仮定せず、現在の公式仕様と実接続を確認します。最新資料・今回の実観測と未検証範囲はリポジトリの検証記録へ分けて残します。

## 条件付きlive pilotの再開手順

以下は実ホストが専用Codexクラウドの操作を許し、対応する公式入口・接続・対象環境が実確認された場合に、rootの同じ経路所有者が手動で行う順序です。helperのpreflight完了だけでは開始しません。フックから自動起動せず、利用不能・許可不明なら不足理由を残して止めます。Work、モデルAPI、別CLIセッションへの切替で補いません。

1. 元依頼記録で所有者、request ID/hash、許可範囲、task IDと送信状態を確認します。既存taskがあれば同じtaskを照合し、新規execを実行しません。結果不明なら先に元の実行結果・限定した対象情報から照合します。一致するtaskを特定できなければ未解決のまま返し、再送しません。
2. 今回のCLI/UIで公式入口と操作を確認します。2026-10-20にローカルCLI `0.163.0-alpha.10.2` のhelpだけで観測した構文は下記です。helpの成功は認証や接続成功ではありません。published environment、新規workspace、既存taskのsaved stateを区別し、repo・権限・environment・テスト環境・branchが期待revisionを指すことを実確認します。execのbranch指定はcommit SHA固定機能の証明ではなく、branch更新競合も確認します。モデル/effort指定の反映保証は未確認です。
3. 承認済み材料を同じpromptに固定し、dispatch-planの不足を解消します。実送信直前に元記録へ送信中/unknown_outcomeを残し、cloud_run.pyの明示dispatchで一度だけ送ります。返却task IDを同じ記録へ保存してsentへ進めます。timeoutやID不明はunknown_outcomeのままにし、execを繰り返しません。`--attempts 1`はbest-of-Nのassistant attempt数で、無制限の再試行指示ではありません。
4. 同じtask IDのstatusを確認します。既存の読取り範囲でreceiveの不足を解消し、終わった元taskのdiffと結果・検証証拠を回収します。必要な場合だけ対象environment/件数を限定したlistを使い、他の会話履歴を一括収集しません。listだけで依頼同一性を推定しません。attemptを選ぶ場合は同じ1-based番号をdiff/apply/結果記録へそろえます。
5. repo/base revision、実差分、実行したcheckとその環境/結果、合格条件を意味確認してacceptへ進みます。diff表示だけではテスト実行済みと扱いません。ローカルテストに適用が必要なら、承認済みの隔離worktreeに検証用として適用して検証し、最終反映と区別します。受入前に本来の作業checkoutへ適用しません。
6. 受入済みの変更を本来のcheckoutへapplyする必要があれば、最新の期待HEAD/status、適用先、許可範囲を確認して別操作として実行します。commit/push/PR/merge/deployは各許可範囲と合意終端に従います。最後に元依頼のcollected/accepted・未解決点・証拠・再開先を更新し、今回開いた完了タブを閉じます。

```powershell
codex cloud exec --env <ENV_ID> --branch <BRANCH> --attempts 1 -
codex cloud status <TASK_ID>
codex cloud diff <TASK_ID> --attempt 1
codex cloud list --env <ENV_ID> --limit 5 --json
codex cloud apply <TASK_ID> --attempt 1
```

execのQUERYは位置引数、status/diff/applyに観測済み`--json`はありません。listは`--env`、`--limit`（1–20、既定20）、`--cursor`、`--json`をhelpで確認しました。上記は操作順を説明する構文例で、一括実行するスクリプトではありません。今回これらの実コマンドによるタスク送信・list・認証・applyは行っていません。状態管理は所有者が元記録に残す契約であり、この手動例自体に永続ledgerはありません。新規実行はcloud_run.pyのclaim管理を使い、raw execを重ねません。

## result / acceptanceの形式

resultはschema_version、request_id、input_sha256、handoff_sha256、task_id、status、repository、base_revision、change_kind、output_revision、diff_evidence、checks_completed、verification_status、verification_evidence、artifact_referencesをすべて持ちます。

- status: completed/failed/partial/unverified。failed/partial/unverifiedをcompletedへ丸めません。既知の失敗/部分結果は所有者レビューが未完了でも保持し、owner_reviewへ意味確認の到達点を別に返します。
- repository/base_revision: 実際に使用したrepoと開始revision。handoffと照合します。output_revisionだけでは入力checkoutを証明しません。
- change_kind: changed/read_only/no_change。changedは許可されたeditと、baseから変わったoutput_revisionまたはdiff_evidence（未commit差分等への参照）が必要です。read_only/no_changeは明示した状態として扱い、差分参照を持たず、output_revisionはnullかbaseと同じ値にします。変更不要が合格条件を満たすかは所有者が意味確認します。
- checks_completed: 実行した必須検証の名前の配列。verification_statusはpassed/failed/partial/unverified/not_requiredです。必須検証があればpassed、全必須check、空でないverification_evidenceが必要です。必要な検証がなければnot_requiredを使えます。
- diff_evidence/verification_evidence/artifact_references: 秘密値を除いた既存証拠への参照配列。実行コマンド・cwd・環境・結果・時刻・revision/対象差分を必要に応じ記録します。CLI diffだけはテスト実行証拠になりません。

acceptanceは`owner_id`、`decision`（accepted/needs_changes/unverified）、`criteria_checked`（true/false/null）、`reference`（既存の意味確認の参照）です。同じ所有者が合格条件・実差分・実検証を確認し、重要例外はrootへ返します。ID/hashの一致やメタデータの自己申告だけで受入しません。helperの照合成功はsemantic acceptanceやlive testを代替しません。通常修正は同じ担当へ返し、別セッションへ移るだけで未確認を解消した扱いにしません。
