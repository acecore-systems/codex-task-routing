# 明示クラウドCLI transport

`cloud_run.py`は、現在のホストがCodex Cloudの独立実行を許し、元依頼の対象・材料・操作が承認済みの場合だけ明示的に呼ぶ入口です。フック、通常Chat、標準子の起動規則から自動的に呼びません。この版のCLI adapterは実装済みですが、このadapterによる実クラウドへの送信・認証・結果回収は未検証です。導入やJSONの宣言によってホストの許可を発行しません。現在のホストが禁止・不明なら、実装済みでも送信せずrootへ返します。

`session_plan.py`の既存出力は維持します。`planning_only:true`、`runtime_implemented:false`は計画helper自身がCLIを実行しない意味であり、この別transportが未実装という意味ではありません。`preflight_complete`は供給された観測の照合だけです。クラウドの実checkout、本人承認、CLI binaryとsourceの同一性、テスト成功は別に確認します。

## ネイティブUIは別経路

この文書は`codex_cloud_cli`専用です。現在のネイティブCloud画面を使う`codex_cloud_ui`の[計画・送信前claim・元task回収](session-routing.md#ネイティブcloud-uiとcliを分ける)とは区別します。UIのタブ/task UUIDをCLIへ渡さず、CLIの404/exit 1/空stdoutを理由にUIへ再送・新タスク化しません。同じbackendと未確認なら、元UIの同一taskと保存記録へ戻ります。通常Chat/Work/APIへfallbackしません。UI所有者も送信前の永続claim・依頼ID/hash・元タブ/task保存を行い、CLI runnerがそのUI連携を自動化すると仮定しません。

元依頼の有効な同じ対象・材料・操作の承認は再利用します。この経路分離は新しい独自承認を要求するものではなく、元依頼の範囲と実ホストの要求を維持するものです。

## 明示呼出し

```powershell
python <plugin-root>/scripts/cloud_run.py dispatch --handoff <handoff.json> --live <live.json> --prompt <approved-prompt.txt> --cwd <absolute-repo-worktree> --state-dir <absolute-private-state-dir> --codex <absolute-native-codex-executable> --protocol codex-cloud-0.163-alpha10
python <plugin-root>/scripts/cloud_run.py inspect-record --handoff <handoff.json> --cwd <absolute-repo-worktree> --state-dir <same-private-state-dir>
python <plugin-root>/scripts/cloud_run.py receive --handoff <handoff.json> --live <receive-live.json> --cwd <absolute-repo-worktree> --state-dir <same-private-state-dir> --codex <absolute-native-codex-executable> --protocol codex-cloud-0.163-alpha10
```

計画用handoff/liveは[既存契約](session-routing.md)と同じです。runnerは`codex_cloud_cli`だけを受け付け、UI/Work/APIへ切り替えません。root/owner/depthと操作別の既存承認を照合しますが、入力の宣言は実権限の証明ではありません。新しい承認を毎回求めず、現在も有効な同じ対象/操作の承認を再利用します。

`input_sha256`は`--prompt`ファイルの**exact UTF-8 bytes**のSHA-256です。改行・BOMを正規化しません。空、NUL、非UTF-8、128KiB超過、hash不一致は拒否します。依頼ID/hash、実repo/base revision、不一致時停止、許可操作、終端・必須検証・返却条件を固定契約として添え、`wire_sha256`を別に記録します。元本文はそのまま末尾へ置きます。本文や秘密値をrecordへ保存しません。認証情報・Cookie・OAuth JSON・私的画面をpromptへ含めない責任は所有者にあり、このhelperは秘密検出器ではありません。

native呼出しは`shell=False`のargv、`cloud exec --env … --branch … --attempts 1 -`とstdinのbytesを使います。promptをプロセスの引数へ載せず、メタ文字やWindowsの引数長・quotingに依存させません。Windowsでは`.cmd`/`.bat`を拒否し、native `.exe`への絶対パスを要求します。モデル・effort指定や設定上書きは加えません。標準CLI自身が既存認証を使い、runnerは認証ファイルを読みません。`CODEX_CLOUD_TASKS_BASE_URL`/`CODEX_CLOUD_TASKS_MODE`が設定されている場合は値を出さず、mockやscope外endpointへ送る前に拒否します。

## ローカル・remote revisionの確認

送信前に指定cwdがrepo root、branch/HEADがhandoffと一致、worktreeがclean、originが同じGitHub repo、`git ls-remote --refs origin refs/heads/<branch>`が期待revisionの1行だけであることを確認します。別worktree探索、fetch、checkout/resetで不一致を補いません。branchとIDはoptionとして解釈される`-`開始を拒否します。GitHub HTTPS/SSHの同一性だけを正規化し、認証入りURLは拒否・非保存にします。

remote確認後もbranch更新競合があるため、これをクラウド側の開始HEAD証明としません。`--branch`にcommit SHAを渡せるとも仮定しません。固定prompt契約はクラウド担当に実repo/開始HEAD検査と不一致時停止を要求し、結果受入で実repo/base_revisionを別確認します。receiptの`cloud_checkout_verified:false`はその未確認を保持します。

## 永続claimと並行・結果不明

`--state-dir`はcallerが管理する非公開ローカルディレクトリです。runnerは親ディレクトリの`.git`、リンク/reparse pointを拒否し、Git checkout内へrecord/diffを保存しません。POSIXでは作成modeを制限しますが、**Git外であることはWindows ACLの確認ではありません**。ホスト側で保存先のアクセス権・同期/共有先を確認します。稼働Codexホームへのインストールや設定書換えは行いません。

request IDのhashごとのディレクトリで、固定inodeのOS lockと永久`O_EXCL` claimを使います。local/remote確認後、execの**前**に`unknown_outcome`をfsyncしてから一度だけ送ります。task URLを厳密に読めた場合だけ元task IDとsentを原子的snapshotへ保存します。claimは成功・timeout・crash・壊れたresponseでも削除しません。partial/corrupt claimやrecord不一致も送信を止めます。lockの期限やCLIの失敗を再送理由にしません。

同じrequest IDの再呼出しは永続recordを優先し、入力handoffが古いnot_sentでも新規execへ進みません。scope/material/wire hash/cwd/revision/environment/ownerが変われば拒否します。snapshot保存失敗時も永久claimが再送を止め、解析済みIDがあれば`observed_task_id`として返すだけで保存済みとは扱いません。結果不明・ID不明はrootが元依頼を照合し、IDを推測しません。runnerにclaim解除・state巻戻し・ID差替え・自動再送コマンドはありません。

この記録は署名付きのサービス側idempotency keyではありません。同じ保存先/IDを使う呼出しの重複を防ぐローカル機構です。保存先変更、ID変更、外部からの削除・改変、raw execの併用まで防いだとは主張しません。別taskとして再投入して結果不明を迂回しません。

## 固定source protocolと結果回収

profile `codex-cloud-0.163-alpha10`は、公式tagのcommit [`cd643519fb56011557d09b41053a3a4c0e013e1f`](https://github.com/openai/codex/blob/cd643519fb56011557d09b41053a3a4c0e013e1f/codex-rs/cloud-tasks/src/lib.rs)で確認した構文を実装します。installed CLI `0.163.0-alpha.10.2`とこのsourceのbinary同一性は未証明です。profile指定は互換性やホスト許可の証明ではありません。適合を確認した実行ホストだけで選び、実応答が非一致なら停止します。

- exec: stdout全体が単一の`https://chatgpt.com/codex/tasks/<ID>`の場合だけ採用。空、説明付き、複数URL、別host、query付き、不正UTF-8等はunknown_outcomeです。失敗やtimeoutの出力からIDを拾いません。
- status: 3行human textを検査し、`[READY]`はexit 0、`[PENDING]`/`[APPLIED]`/`[ERROR]`はexit 1というsourceの契約を使います。PENDINGは待機、ERRORは既知の失敗、APPLIEDは独立状態として保持します。READYはテスト合格を意味しません。
- statusはtask IDを返信しません。元ledgerと固定argvで対象を絞りますが、実reply IDをattestしたとは扱わず、`reply_task_identity_attested:false`を返します。title/env labelからIDや環境同一性を推測しません。
- receive: 新規送信の承認・材料送信・利用枠を要求し直さず、元taskのhost/receive能力/読取り承認だけを確認します。PENDINGではdiffを呼ばず、その他の既知状態は同じtask IDの`diff --attempt 1`を回収します。apply/list/commit/push/merge/deployは呼びません。

stdout/stderrを画面や通常ログへ転記しません。stderrは破棄、stdoutは制御用の厳密解析または最大16MiBのdiff bytes保存だけです。失敗診断は固定形式です。diffはhash付きファイルへexact bytes保存し、metadataにhash/byte数/相対filenameを残します。空diffも勝手に不存在・変更不要へ読み替えません。保存しただけでvalid patchやテスト実行・成功とは扱いません。

## 内容受入

このtransportはstatusとdiff bytesの回収までです。read-only成果物、実テストログ、環境、実repo/base_revisionを返すAPIは追加していません。CLI diffから得られない証拠は所有者が承認済みの正式経路で別途取得するまで未確認です。receiptの`verification_status`は常にunverifiedです。

永続recordの元task ID/到達点を同じhandoffへ反映し、既存`session_plan.py cloud --phase accept --result … --acceptance …`で照合します。所有者は実差分・実repo/base revision・必須checkの実行環境/結果・終端条件を意味確認します。必要な検証用適用は承認された隔離worktreeで別操作として行い、受入前に本来のcheckoutへ適用しません。transportは受入や最終反映を自動化しません。失敗・部分・未検証はその状態でrootへ返し、通常修正は元の担当で継続します。
