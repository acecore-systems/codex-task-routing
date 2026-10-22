# 既存Codex Cloudへのnative継続と回収

0.18.0の`cloud-native`は、実際に観測した既存Codexタスクへの継続・回収を計画するローカルhelperです。対象は今回確認した`kind:codex` / `host_id:durable`の既存taskに限り、任意のCloudホストや新規作成APIには対応しません。`create_thread`の`chatgptWorkCloud`は別の実行先、`handoff_thread`のCloud移管も非対応です。Work・旧Cloud CLI・モデルAPIへの切替は行いません。UIの`cloud` schema 1と`ClaimStore`保存schema 2は変更しません。

新しい方針が読み込まれ、現在のホストもnative操作を許すことをrootが確認してから実行します。旧UI-onlyの上位指示が注入中のターンでは、コードの存在や本人の作業依頼だけでその制約を解除せず、計画・検証を返します。子は提案と不足をrootへ返し、ツールを呼びません。利用者の同じ対象・材料・操作への有効な承認は再利用します。親のmodel/effortを変えず、native送信のmodel/thinkingも省略します。

## 固定する入力

次は架空の既存taskへの最初のnative継続です。実際のID・観測・承認ではありません。

```json
{
  "schema_version": 2,
  "request_id": "native-request-1",
  "input_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "owner_id": "root-owner",
  "return_to": "root-task",
  "repository": "example/project",
  "branch": "codex/example",
  "expected_revision": "1111111111111111111111111111111111111111",
  "environment_id": "environment-1",
  "endpoint": "指定範囲の読取りとテスト結果を返す",
  "acceptance_criteria": ["必須検証の結果と実環境が確認できる"],
  "required_checks": ["python -m unittest"],
  "allowed_operations": ["read", "test"],
  "target": {"kind": "codex", "host_id": "durable", "task_id": "original-task"},
  "previous_request_id": null,
  "state": {"submission": "not_sent"}
}
```

`input_sha256`は承認済み依頼本文・材料の識別hashです。経路情報の見出しへrequest ID/hashを添える場合、その見出しは識別対象材料の外側にし、hash自身を含む文面の自己hashを要求しません。実送信する本文が承認済み材料から変わっていないことは所有者が照合します。開始cwd・repo/branch/期待revision、README・AGENTS、cloud-environment runtimeスキルとnetwork policy、必須検証・許可操作・停止条件を本文に含め、応答にも今回のrequest ID/hashを付けるよう依頼します。[共通開始手順](session-routing.md#現行cloud-uiの標準手順)を再利用します。

`previous_request_id:null`は、この保存契約での最初のnative依頼だけです。既存UI依頼や結果不明の保存記録を無視してよいという意味ではなく、rootが元taskと既知の前依頼を照合します。次の継続は回収済みの前native request IDを指定し、前記録を保持します。scope変更は別の明示依頼・別claimとして同じtaskへ継続できます。未回収の前依頼や結果不明を新しいIDに置き換えません。

`native_handoff_sha256()`はstateを除く正規化JSONを識別します。host/task、環境、scope、前依頼、材料の変更は別hashです。stateはnot_sent / unknown_outcome / sent / collected / acceptedだけです。元taskはtargetに固定済みなので、state.task_id・tab_id・create等の追加項目を受理しません。

## 段階ごとの計画

```powershell
python <plugin-root>/scripts/session_plan.py cloud-native --handoff <handoff.json> --phase inspect --live <live.json>
python <plugin-root>/scripts/session_plan.py cloud-native --handoff <handoff.json> --phase dispatch-plan --live <live.json>
python <plugin-root>/scripts/session_plan.py cloud-native --handoff <handoff.json> --phase receive --live <live.json>
python <plugin-root>/scripts/session_plan.py cloud-native --handoff <handoff.json> --phase wait --live <live.json>
python <plugin-root>/scripts/session_plan.py cloud-native --handoff <handoff.json> --phase accept --result <result.json> --acceptance <acceptance.json>
```

liveはschema_version:2、handoff_sha256、transport:`codex_cloud_native`、actor（role/root、depth/0、executor_id/所有者）、observed_at、target、checksを持ちます。targetはhandoffと一致させ、`read_thread`の実結果のkind/host/taskと照合します。観測は5分以内・未来時刻不可です。宣言やhashはホスト権限の証明ではありません。

checksの値は`{"state":"unknown"}`等、確認済みなら`{"state":"verified","reference":"既存の対象を限定した観測参照"}`です。stateはunknown / verified / permission_checked / blocked / unavailable / confirmation_requiredで、未指定はunknownです。verified / permission_checkedにはreferenceが必要です。

| 段階 | 必須checksと扱い |
| --- | --- |
| 共通 | native_policy、host_permission、target_identity。新方針の有効性・現ホスト許可・観測した既存Codexを分ける |
| inspect | inspect_capability、inspect_authorization。送信や新しい材料転送の承認を要求し直さずread_threadで元taskを確認 |
| dispatch-plan | dispatch_capability、target_authorization、materials_authorization、environment、repository_revision、test_environment、previous_submission |
| receive | receive_capability、receive_authorization。送信やwait能力は要求せず同じ元taskの今回応答をread_threadで回収 |
| wait | wait_capability、wait_authorization。未確認・利用不能ならplan_receive_original_task。次のreceiveで読取り権限を確認 |

target_identity、previous_submission、environment、repository_revision、test_environmentは実観測のverifiedが必要です。その他はverifiedまたはpermission_checkedを受理します。dispatch_capabilityのpermission_checkedは、汎用ツールschemaと今回操作の呼出し条件を確認した意味であり、durableへの送信成功を主張しません。ツールが見えること、対象承認、実Cloudでの実行成功は別です。waitの汎用エラーを存在しないtaskや未送信の証拠にせず、同じ元taskのreadへ戻ります。

helperは常にplanning_only:true / runtime_implemented:false / execution_authorized:false / creation_supported:falseです。preflight_complete:trueも供給された条件の照合結果であり、実送信許可や成功ではありません。dispatch-planはclaim_then_call_native_tool_onceを返します。unknown_outcome/sent/collected/acceptedなら新規送信を拒否し、元依頼の照合・回収・受入へ返します。

## rootが正式ツールを一度呼ぶ手順

1. rootが現在有効なnative方針と実ツールschema、本人の同一範囲の送信指示を確認し、`read_thread`で既存Codexのkind/host/task、到達点・環境・対象revisionを観測します。元UIの既知claimや前native依頼も照合し、未解決なら送信しません。
2. 承認済み本文と固定scopeのhashを照合し、dispatch-planを実行します。不足があればfalseをtrueへ丸めず、その不足を返します。
3. Git管理外のprivate保存先で下のNativeClaimStoreを使います。lock内でread/claimを行い、この呼出しで新しいclaimを作成した同じ所有者だけが、一度の正式送信へ直ちに進みます。送信前にclaimとunknown_outcomeが永続化されます。中断・担当交代・再開後に新規claimだったことを再利用して送信しません。
4. lockを抜けて親自身が`send_message_to_thread({hostId:元host_id,threadId:元task_id,prompt:承認済み本文})`を一度呼びます。model/thinkingは省略します。helperやフックはこのツールを呼びません。返答が曖昧・timeout・保存失敗ならunknown_outcomeを保持し、再送しません。
5. 実送信の受領を確認できた場合だけ、最新記録をlock内で読み直してsent/submission_recordedへ保存します。ツール受領はテスト実行成功ではありません。回収は同じhost/taskへ`read_thread`を使い、今回のrequest/hashに対応する新しい応答を照合します。waitが実際に利用可能な場合だけwait計画を使い、失敗はreceiveへ戻ります。
6. 回収済みをcollected/collection_recorded、所有者の実差分・テスト・合格条件の意味確認後をaccepted/owner_review_recordedとします。古い完了結果・thread idleだけで今回依頼を完了にしません。

```python
from cloud_record import NativeClaimStore

store = NativeClaimStore(private_state_dir, request_id, target)
with store.lock():
    existing = store.read()
    if existing is not None:
        # 新規送信せず元依頼の照合・回収へ返す。
        raise RuntimeError("original_request_must_be_reconciled")
    claimed = store.claim(binding)
# この新規claimを作成した所有者が、承認済みの正式app toolを一度呼ぶ。
# helperからは送信しない。中断・再開時は上のreadが既存claimを返す。
```

bindingはrequest_id/input_sha256/handoff_sha256/cwd/repository/branch/expected_revision/environment_id/owner_id/target/previous_request_idです。cwdはCloudで開始する絶対cwdです。保存schema 3はtop-levelにschema_version/binding/submission/task_id/eventを持ち、task_idは最初からtarget.task_idと一致します。tab_idは受理しません。全read/claim/saveはlock内で行い、実ツールを呼ぶ前後を同じ所有者が対応付けます。保存先のprivate ACL・リンクなし・Git管理外を確認します。

request IDごとの永久claimと、同じtaskのmutex・前依頼ごとの永久後続枠を使います。最初のnative依頼枠も1つだけです。前依頼がcollected/acceptedになるまで次を作れず、同じ前依頼へ複数の後続も作れません。後続枠を予約してからclaim保存前にcrashした場合も枠を残し、別IDによる送信を止めます。scope/host/taskの変更、状態の巻戻し、未知schema・破損記録・claimなしのsnapshotは拒否します。旧UI保存記録を削除・移行・native tabへ変換しません。

## 今回の応答の受入

resultはUIの共通result項目にtarget、response_reference、correlation_evidenceを加えたschema 2です。task_idとtargetは元handoffのhost/taskへ一致させます。response_referenceは実際に取得した今回のturn/message等への参照、correlation_evidenceは今回のrequest ID/input hashとその応答が対応する既存証拠の参照配列です。completedを受け入れるには空でない両方が必要です。toolがそれらを返さず対応確認できない時はunverifiedのまま返します。

acceptanceはUIと同じowner_id / decision / criteria_checked / referenceです。実repo/base revision、変更差分、実行した必須テスト・環境・結果を照合し、所有者の意味確認を別に行います。要約だけで必須証拠が取れない場合は追加のreadまたは同じ担当への承認済み修正へ戻り、再送で埋めません。[UIの共通result条件](session-routing.md#result--acceptanceの形式)も適用します。
