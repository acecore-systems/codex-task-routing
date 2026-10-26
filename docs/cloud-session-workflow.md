# Cloudセッションの運用（0.19.0）

本書は現行の[session-routing.md](../plugins/codex-task-routing/skills/task-routing/references/session-routing.md)、[native-cloud.md](../plugins/codex-task-routing/skills/task-routing/references/native-cloud.md)と、`session_plan.py` / `cloud_record.py`の契約を要約した手順です。実操作の成功記録ではありません。版・環境ごとの実証範囲は[検証記録](validation-0.19.0.md)と分けて扱います。

## 判断と担当の継続

- 新規の独立したrepo実装・検証は、材料・環境・必須テスト・ホスト条件が適合すればCloudをProより先に選びます。親でも実行可能という理由だけで除外しません。
- 同じ完了単位の適任担当は、調査・修正・検証・合意済み指摘対応・受入まで継続します。status要求や待機は新規目的ではありません。短い密結合の仕事は継続し、同一目的の限定補助は標準子の条件で判断します。
- 長時間・長文脈・利用枠回避だけでは分割しません。Cloudに不適合なら不足と代案を示し、repo以外の分析等は有効なProを検討します。推薦は実行許可ではなく、元依頼の同一範囲の承認と実ホストの制約を照合します。

## repo環境とtaskを分ける

環境は再利用するセットアップ、taskは完了単位の作業状態です。環境名や既存taskの存在だけで対象repo・準備状態を推測しません。

| 環境状態 | 次の確認・操作 |
| --- | --- |
| `unknown`（省略時も同じ） | 対象repoの既存環境を調査。権限エラー・一覧取得失敗を不存在にしない |
| `absent` | 対象repo用の環境の不存在を確認済みの場合だけ使用。対応UIの機能と許可があれば作成・準備・テスト・保存・公開し、公開を確認 |
| `existing` | repo対応を照合して再利用。`ready:null`は調査、`ready:false`は許可と機能を確認して準備・テスト・再公開、`ready:true`は引継ぎ準備 |

`ready:true`は今回必要なrepo/revision・依存・テスト・ネットワークを利用できると確認した状態です。不存在だけでCloud自体を利用不能にしません。repo不一致やsetup機能・許可不足は解消まで進めず、推薦のtop-level `next_action`を優先します。下位の`cloud_lifecycle`は条件付きの順序です。

独立目的の新taskは対応するCloud UIで作成します。同じ完了単位は元taskを継続します。nativeは観測済みの既存Codex durable taskへの継続・回収だけで、新規作成には対応しません。rootが有効なnative方針・現ホスト許可・対象を確認し、正式ツールを使います。子は提案と不足をrootへ返します。

## 並列開始と引継ぎ

同一repoでも、独立scope、分離branch/workspace、統合担当、委譲者自身の有用な独立作業、ホストの並列許可・上限を確認できれば並列化できます。共有ファイル・依存差分・外部状態の衝突は開始前に調整します。branch名の違いだけでworkspaceの分離を証明せず、条件がfalse/unknownなら先に調整します。

各依頼本文には開始の絶対cwd、repo/branch/期待HEAD、README・AGENTS、cloud-environment runtimeスキルとnetwork policyの確認、編集scope、必須検証、合格条件、許可操作、停止条件、返却先を含めます。開始時にpwd/status/branch/HEADを実測し、不一致や既存変更があれば依頼の停止条件に従います。自動start_skill注入やHTTP listenerを前提にしません。

## claimと再送防止

送信前にGit管理外の非公開保存先へ、依頼ID・材料hash・scope・所有者・環境・元タブ/taskを固定した永久claimを保存します。`ClaimStore`（UI）または`NativeClaimStore`（native）のread/claim/saveはlock内で行い、新規claimを作成した同じ所有者だけが一度の正式送信へ進みます。フックとplannerは送信しません。

claimは送信前から`unknown_outcome`を残します。送信済み・timeout・結果不明・保存失敗・担当交代・再開は再送理由になりません。元依頼を照合・回収し、IDや保存先の変更、`not_sent`への巻戻しで迂回しません。未知・破損・旧形式の記録は停止してrootへ返します。nativeでは元host/taskと前依頼を固定し、前依頼の回収前に次の依頼を作りません。waitが未確認・失敗なら同じ元taskの読取りへ戻ります。

## 実結果の回収・受入・cleanup

1. 元依頼ID/hashと今回の応答を照合し、実repo/base revision、変更差分、出力revision、必須検証のコマンド・環境・結果・時刻と証拠を回収します。nativeは今回の応答参照と対応証拠も必要です。
2. 同じ所有者が合格条件と実差分・検証を意味確認します。ツール受領、idle、過去の完了応答、plannerの照合成功だけでは完了にしません。失敗・部分・未検証・skipを保持し、通常修正は元担当へ戻します。
3. 統合担当が各成果を確認し、適用先の最新HEAD/statusと許可範囲を照合します。隔離環境での検証用適用と最終反映を区別し、commit/push/PR/merge/deployはそれぞれの承認と合意終端に従います。
4. 回収後は`collected`、内容受入後は`accepted`として証拠・未解決点・再開先を残し、今回開いた完了タブを閉じます。cleanupで永久claimや履歴を消さず、他担当のbranch/workspace・既存変更を破棄しません。

Work・旧CLI・モデルAPI・模擬環境への切替でCloudの不足を埋めません。UI環境作成・公開、新task作成、native継続、実テスト、内容受入は別々に実証します。ローカル検査や過去版の成功を現行Cloud全体の成功に読み替えず、公開文書には個人パス・実task ID・秘密値・実行ログを含めません。
