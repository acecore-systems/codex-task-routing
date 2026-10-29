# 変更履歴

## 0.22.0 — 任意の同モデル委譲ガード（既定オフ）

- 配布挙動の追加です。方針の意味、既定のモデル・effort、通常Chatの条件、明示overrideは変更しません。
- 子のエージェントが`spawn_agent`（Agentツールを含む）で、親と同じモデルを明示して委譲する場合を、PreToolUseフックで検出します。既定はオフで、オフのときは何も出力せず何も書きません。`<CODEX_HOME>/codex-task-routing/spawn-guard.json`で利用者が`observe`（記録のみ）または`enforce`（同モデルと確定したときだけ拒否）を選びます。
- 判定は、呼び先の`model`引数が呼出し元と（前後の空白を除いて）完全一致し、`agent_type`の指定がない場合に限ります。省略・役割設定による上書きの可能性・入力不備は判定不能として素通しし、上位役割の判定はしません。記録は値を含まない判定結果だけです。
- PreToolUseの追加によりフック定義が変わるため、更新後に一度`/hooks`での再信頼が必要です。defaults・policy_revision・SessionStart/SubagentStartの注入文は変更しません。PreToolUseは完全な強制境界ではなく、フック対象外の経路は止められません。[検証範囲](validation-0.22.0.md)を参照してください。

## 0.21.1 — 使用量比較の根拠を明確化

- 既存の観測完了記録を使う読取専用の比較CLIを追加し、親と親子合計、品質・手戻り・Chatを分けて扱います。実使用量の自動取得は行いません。[検証範囲](validation-0.21.1.md)を参照してください。
- 現行方針に合わせてリポジトリのガイダンスを整えました。

## 0.21.0 — Terraを通常の委譲先に変更

- 通常の実装・修正・デバッグ・探索・複数ファイル作業・調査下書きはTerraを既定の委譲先にします。合意済み設計の具体化や能力不足では理由を記してSolを選びます。既存担当の継続、Cloud/Proの優先条件、モデルとeffortは維持します。
- Solは設計を具体化し、ホストの許可と深さが許す場合は実装をTerraへ委譲して成果をレビュー・統合します。Terraは方式選択・合意前提の変更を親へ返し、Solへ直接再委譲しません。標準孫はAstra→Sol/Terra/Luna、Sol→Terra/Luna、Terra→Lunaまでです。
- 通常Chatの所有者は引き続きrootまたはroot直下Sol子です。旧terraや文章overrideは自動移行せず、[検証範囲](validation-0.21.0.md)と稼働環境の版を区別します。

## 0.20.0 — Terra 6.1の正式候補追加

- 新キー`terra61`を`gpt-6.1-terra/xhigh`の実装・修正・テスト候補に追加。Solと同列に選び、実測のない性能・品質・費用の優劣は断定しません。適合する既存担当の継続、Cloud/Proの優先条件は維持します。
- 旧`terra`はschema 1の互換専用で、既存overrideを新候補へ自動移行しません。Luna・Sol・Astraと親の実設定は変更しません。
- 標準孫はAstra→Sol/Terra/Luna、Sol/Terra→Lunaまで。Sol/Terra間の再委譲は不可で、通常Chatの所有者はrootまたはroot直下Sol子だけです。
- 候補表・要約・詳細・スキル・両フック・診断を同期。古い文章overrideを保持したまま新候補と注意を表示し、意味の矛盾はrootへ返します。稼働設定を自動で変更しません。
- [検証結果と未確認範囲](validation-0.20.0.md)。

## 0.19.0 — 独立repo工程をCloud優先にし、環境再利用と並列taskを明確化

- 方針の意味変更: 新規の独立repo実装・検証は適合するCloudをProより先に選びます。同じ完了単位の適任担当は継続し、Cloudに不適合な工程やrepo以外の分析等は有効なProを先に検討します。
- repo環境のunknown / absent / existingを区別。未確認は調査、確認済み不存在は対応UIで作成・準備・テスト・公開、既存はrepoと公開/準備状態を確認して再利用します。環境の不存在だけでCloudサービスを利用不能にしません。
- 独立目的は既存環境から新セッション、同じ完了単位は元taskへ継続します。同一repoの複数セッションは独立範囲・branch/workspace分離・統合担当とホスト条件を満たせば並列利用でき、衝突は開始前に調整します。
- `recommend`の任意入力と条件付きlifecycle出力、回帰テスト、defaults/注入文/SKILL/手順/template/fixtures/provenanceを同期。モデル/effort・認証・既存UI/native claimを維持し、新規native作成、Work/旧CLI fallback、hook送信は追加しません。[検証記録](validation-0.19.0.md)。

## 0.18.1 — 配布修正: 自動更新の子プロセスを短い固定の作業ディレクトリで起動

- 配布修正です。方針の意味、既定のモデル・effort、通常Chatの条件、明示overrideは変更しません。
- 自動更新がmarketplaceを確認・更新する子プロセスの作業ディレクトリを、Windowsでは`CODEX_HOME`の長さに依存しないWindowsディレクトリにしました。作業ディレクトリが259字以上だと子プロセスが起動できず、`CODEX_HOME`が長いと更新が`skipped/cli_error`として黙って失敗していました。
- 子プロセスへ渡す設定は絶対パスの環境変数だけです。Windows以外は従来どおり状態ディレクトリを使います。作業ディレクトリが無い場合は子を起動せず失敗として記録します。
- READMEに、Windowsで`CODEX_HOME`を約230字以内にする注意を追加しました。定期タスクの登録と更新処理の入口の作業ディレクトリは既存登録との整合のため変えていません。
- [検証記録](validation-0.18.1.md)。

## 0.18.0 — 既存Codex Cloudへのnative継続と回収

- 方針の意味変更: 観測済みの既存Codex durableタスクへ、rootが正式app toolで継続・回収する経路を追加。新規Cloud作成、Workや旧CLIへの切替は含めません。現在の上位指示がUI-onlyなら、そのターンの実送信は行いません。
- `cloud-native`はschema 2の非送信planner。方針・ホスト許可・kind/host/task実観測・材料と前依頼を段階別に確認し、ツール能力・送信受領・実行・受入を分離。wait未確認や失敗は元taskのread計画へ戻します。
- `NativeClaimStore`はschema 3でメッセージごとの依頼hash・scope・元host/task・前依頼を固定。永久claimとtask mutex・単一後続枠で、結果不明・保存失敗・crashを別IDや再送で迂回しません。UIのschema 1／保存schema 2は互換維持します。
- defaults・スキル・手順・fixtures/provenance・配布検査を同期。0.17の委譲規則、親設定・モデル/effort・override・通常Chat条件は維持します。[検証記録](validation-0.18.0.md)。

## 0.17.0 — 委譲結果の裏取りと返却形式

- 方針の意味変更: 子の報告に含まれるファイル名・関数名・API・数値・コマンドなど確かめられる事実は、親が裏を取ってから採用し、重要な判断に直結するものは親が自分で確認します。ユーザーへ技術的な選択肢を示す・質問する前に、担当に他の方法を聞いて選択肢を出し切ります。長い成果物は子に指定ファイルへ書かせ、親はパスと要点だけを受け取ります。
- 分類表に、一般知識のWeb検索を先にLunaで済むか検討する行を追加。一次情報の裏取りが要る時だけ親が行います。
- 方針の詳細・分類表・引継ぎテンプレートと生成fixture/provenanceを同期。SessionStart・SubagentStartの注入要約、親設定・モデル/effort・override・通常Chat条件は変更しません。
- 出典: acecore-systems/claude-task-routing の task-routing ポリシー v1.4.0 の委譲規則を参考にしました。[検証記録](validation-0.17.0.md)。
## 0.16.1 — 配布修正: 長いインストール先でも注入を上限内に

- 配布修正です。方針の意味、既定のモデル・effort、通常Chatの条件、明示overrideは変更しません。
- SessionStart/SubagentStartの注入で、導入先のプラグインルートと方針キャッシュの絶対パスを各1回だけ示し、他の参照は相対パスにしました。長いインストール先で通常Chat有効時の注入が8000字を超え、方針が適用されずsystemMessageになる問題を直します。
- 観測手順は、フックが示すPlugin rootから`scripts/routing.py`を組み立てる説明に合わせました。長いパスの回帰テストを追加し、既存のネイティブ導入検査も同じ表示形式で確認するよう更新しました。
- [検証記録](validation-0.16.1.md)。

## 0.16.0 — 目的単位の推薦とCloud比較

- candidate不要の`session_plan.py recommend`を追加。同じ完了単位の継続担当と親の実行能力を区別し、新規の独立repo実装・テスト・長時間完了単位でCloudを有力候補にします。
- 密結合は継続、同一目的の限定補助は標準子、独立継続管理は別セッション、ローカル依存はローカルを比較します。完結できる通常Chat工程の既存Pro優先を維持します。
- 理由・代案・三値の不足・提案と引継ぎ項目・次の操作を返し、推薦と実作成/送信の承認を分離。既存session/cloud schema、UI claimと再送防止、モデル/effort/overrideは維持します。
- defaults・スキル・catalog・READMEに短い提案例、入力契約、境界ケースを追加し、生成fixture/provenance・配布検査を同期。[検証記録](validation-0.16.0.md)。

## 0.15.2 — 旧Cloud CLIを除去し現行UI専用へ

- 方針の意味変更: Cloud実行を`codex_cloud_ui`だけに限定。旧Cloud CLIの実行コード・専用テスト・専用資料を削除し、plannerの旧transport入力を拒否します。過去の変更履歴・検証記録は保持します。
- UI用に共通claimの最小保存helperを残し、schema 2で依頼ID・材料/hash・scope・元タブ/taskを固定。永久claim・OS lock・binding一致・task不変・巻戻し拒否を維持します。CLI固有のwire hash、attempt/diff保存、status・commandイベントは除去します。
- 旧形式・未知・破損した保存記録は自動削除・変換せず、新規送信を止めてrootへ返します。request IDとstoreの不一致も拒否し、UI手順にlock内のread/claim/saveを明記しました。
- 配布方針・スキル・README・fixture/provenance・package検査を同期し、manifestを0.15.2へ更新。親設定、モデル/effort、通常Chat条件、明示override、実ホストの承認境界は維持します。[検証記録](validation-0.15.2.md)。

## 0.15.1 — 現行Cloud UIと本文での開始手順

- 方針の意味変更: Cloudの標準を現行UIの`codex_cloud_ui`と明示handoffに統一。元の有効な承認、送信前claim、依頼ID/hash、同じ所有者・元タブ/task、一度の送信と同一task回収を維持します。
- 開始時の絶対cwd・repo/branch/期待revision、README・AGENTS、cloud-environment runtimeスキルとnetwork policy、必須検証・許可操作・停止条件を依頼本文へ明記。`start_skill`自動注入に依存せず、HTTP listenerをUIの利用要件にしません。
- 旧`codex_cloud_cli`/`cloud_run.py`は既存記録保全の非推奨互換実装としてコードとテストを保持。自動候補・通常運用の残対応から外し、旧CLI互換性・実送信検証を現行UIの完了条件にしません。実行コード・plannerの入出力契約は変更しません。
- 配布要約・詳細・引継ぎ・スキル・READMEとfixture/provenanceを同期。親設定、モデル/effort、通常Chat条件、明示overrideは維持します。今回のローカル検証と前版の実Cloud成功を分けます。[検証記録](validation-0.15.1.md)。

## 0.15.0 — セッション選択とクラウド計画

- 方針の意味変更: model/effortとは独立した実行環境選択を追加。別目的・独立成果物・別repo/PR・継続管理の利益がある時だけ別セッションを提案し、適合する既存担当を優先します。未確認の担当を不存在にせず、長文脈・枠回避・同モデル移管だけの分割を避けます。標準子と通常Chatの既存制約、親/model/effortと利用者overrideを維持します。
- 配布機能: 外部呼出しのない`session_plan.py`と入力契約を追加。クラウドはinspect/dispatch-plan/receive/acceptを分ける計画helperと、別入口cloud_run.pyの明示CLI transportを備えます。scope、元task、実repo/base revision、変更・必須検証・意味確認を照合し、送信済み/結果不明の再送を認めません。永久claim・OS lock・原子的receiptで同じ依頼の再送を止め、promptはstdin、diffはGit外へexact bytes保存します。実行許可を発行せず、native/live cloud未検証とsource/binary差異を明示します。
- 新規トップレベルタスク・worktree・他タスクへの送信の現ホスト条件を保持し、承認済みの同一範囲は再利用します。Chat opt-inやCLI helpからクラウド権限を推定しません。[検証記録](validation-0.15.0.md)。

## 0.14.0 — 下位役割の標準孫と直下SolによるPro所有

- 標準孫をAstra子→Sol/Luna、Sol子→Lunaへ限定。Luna子・孫から先、同モデル/上位役割は不可。割当て役割とrootからの深さを照合し、不明ならrootへ返す。親の実設定・モデル/effort・overrideは保持。
- 明示opt-inのbrowser経路はrootとroot直下Sol子だけが依頼の準備・送信・回収・内容受入・承認済み修正を所有。Sol→Proは標準孫と別経路、legacyはrootのみ。
- inspect/dispatch/receiveで任意actorを共通照合。旧is_root:trueは互換、false単独・不適合role/depth・所有者不一致・ホスト不許可を拒否。観測は権限やbackendの証明ではなく、追加の本人承認を一律に要求しない。
- 同一タブ/依頼ID/input hashと送信済み状態を引き継ぎ、rootへの回収handoffでも再送しない。並列の独立作業は委譲する担当自身に必要。条件付きの子フックと関連手順・生成物を同期。[検証記録](validation-0.14.0.md)。

## 0.13.0 — 判断に集中する親と同じ担当による完了単位

- 親Sol/xhighを現行基準とする説明を改め、利用者が選んだAstra親は判断・優先順位・担当選定・受入・重要例外へ集中。親の実model・effort、他モデル親、既存overrideは維持。
- 有効な通常Chat Proを新規の適合工程の最初の主担当候補にし、Codex側の現物と反復するまとまった実務はSol 6.1/maxへ。調査・通常修正・テスト・明示承認済みPR・対象指摘対応まで同じ担当で継続。
- 最新revision・差分・到達点・終端・再開条件を引き継ぎ、初回HEADへのresetや通常修正ごとの新規起動を避ける。未到着レビューや新たな承認境界は完了にしない。
- ローカル経路は秘密を扱う登録済み認証工程に限定。ready・全工程未実装・個別段階・実認証を区別し、本人同意・MFA・生体認証等の条件を維持。実行worker・モデルexecutorは追加しない。
- Browserの長文添付化時の本文展開、保存後の表示エラー時の既存reply検証を追記。送受信スキーマ、分類ゲート、モデル/effort設定・通常起動・更新方式は変更しない。表示エラーが再現しなかったためhelperの終了処理は変更しない。[検証記録](validation-0.13.0.md)。

## 0.12.0 — Browserの重複判定と確認・送信・回収の分離

- PR #17が残した必須のtransport_evidenceを撤回し、既存の有効な確認を再利用。旧false・具体的な拒否・未確認は昇格しない。
- 新規入力はbrowser_checksでinspect/send/receiveを確認し、旧booleanと証拠ファイルを重ねて要求しない。permission_checkedは実行成功とは区別。
- --phase inspectは調査だけ、既定dispatchはsend_once、--phase receiveは既存bundleに対するcollect_reply。回収だけなら新規生成枠と新規送信許可を要求せず、回収の許可・モデル・対応照合は維持。
- 不足時に担当・場所・操作・具体的解消手順を返す。公式Browser資料の対応範囲とOutput抽出条項の未解決点を分け、実ホスト未確認・実往復未検証を明示。
- Sol子gpt-6.1-sol/max、親実設定、認証、既定のモデル/effort・通常起動・フック・更新方式は変更しない。[検証と利用環境への引継ぎ](validation-0.12.0.md)。

## 0.11.0 — 通常Chat Proの表示互換と許可根拠

- 選択UIの`Pro`、旧`6 Pro`/`GPT-6 Pro`を共通判定し、既存設定を非破壊で読み込みます。希望条件・観測・backendを区別し、未確認のbackendはunknownとします。
- `transport_authorized`を別Chatへの送受信の確認と明示し、通常Chat内の接続済みツール利用に誤適用しません。根拠不明を公式禁止と説明せず、適用される公式・ホスト・repoの条件を分離しました。
- trueによる候補化には対象方式と送信/回収を支える`transport_evidence`を必要とします。旧liveのtrueだけを確認済みにせず、既存の有効な根拠・承認は再利用します。提供者の例外許可を一律には要求せず、実際の上位禁止・ホスト承認は維持します。
- 規則・文書・回帰fixtureを整合し、13件の境界テストを追加。Sol子`gpt-6.1-sol`/`max`、親設定、認証、無関係な設定・配布方式は維持します。
- [この版の検証記録とローカル更新手順](validation-0.11.0.md)。PR #17の実行結果を配布と利用環境への反映とは区別します。

## 0.10.1 — Sol子のmax化と設定確認の改善

- Sol子の既定effortを`high`から`max`へ変更し、上限も`max`へ拡張。モデルは`gpt-6.1-sol`、親設定・Luna/max・Astra/high・通常Chatの選定条件は維持します。
- 要約と詳細冒頭のeffort直書きを設定参照に置き換え、override後に表と本文が食い違う問題を修正しました。既存の明示overrideは保持します。
- `status --json`に`configured_child_models`を追加し、解決済みの子のID・固定effortと、未確認の実行モデルを区別できるようにしました。状態確認は読み取り専用です。
- READMEと開始時の担当表にもローカル認証経路を明記しました。既存の認証専用の対象範囲と、クラウド選定より先に確認する順序は維持します。
- 個別設定を適用した要約・詳細・両フック・診断の整合性を回帰テストで検証します。配布版の更新と稼働環境への反映は別工程です。

## 0.10.0 — 認証工程のローカル引継ぎ

- 秘密値の入力・保管・転送を要する認証工程を、通常のクラウドモデル選定より先にローカルMCPへ引き継ぐ方針を追加。要約、詳細、分類表、引継ぎ、スキル、両フックの参照をそろえた。公開仕様・秘密を含まない実装は通常の分担対象を維持。
- `chat_plan.py`の任意boolean `requires_private_auth`がtrueなら`local_auth`を返す。CLIはこの場合、Chatのinventory/liveを読み込まない。従来の入力とfalseは既存の分類を維持し、結果を実行許可と扱わない。
- 実行は別途導入済みの`local-auth-lfm`受付protocol `0.3.0`を使う。presetと元セッション・範囲、アーカイブ状態、対応工程を確認し、受付・本人操作待ち・実認証を分ける。単一モデルの共有と排他は実行側に任せる。
- 配布版を0.10.0へ更新。モデルの既定値・親設定・Chat opt-in・フックの定義・信頼を維持し、稼働中の利用環境への自動導入や実サービス認証は行わない。

現在の導入・適用確認・任意機能・トラブル対応は [README](../README.md) を参照してください。各版で確認した環境と未検証範囲は [検証記録](validation.md) に残しています。

## 0.9.1

- Solの既定IDを`gpt-6.1-sol`へ更新し、親の参考基準もGPT-6.1 Sol/xhighへ揃えました。互換用の旧`terra`キーの既定IDも6.1へ更新しました。
- Luna/max・Sol/high・Astra/highの固定effort、既存override、利用者が選んだ親設定、通常Chat 6 Proの条件は維持します。旧`gpt-6-sol`へ固定されたoverrideの確認方法をREADMEに追記しました。
- 今回は既定モデルの更新です。分担原則や配布・更新方式は変更していません。

## 0.9.0

- 既定の子をGPT-6 Luna/max・Sol/high・Astra/highとし、親の参考基準をSol/xhighへ更新しました。利用者の親設定は変更しません。
- 旧`terra`キーはschema 1のoverride互換用に残し、子の割当・有効方針の表示から外しました。既存overrideの5.6系IDは自動変更しません。
- 方針、分類表、引継ぎ、説明、フック出力と検証用fixtureを新しい基準へ合わせました。

## 0.8.2

- release検査をSemVerの正しいprecedence比較へ変更し、同一版、版の後退、build metadataだけの変更、不正な版を拒否するようにしました。
- `status --json` の失敗結果に機械判定用の `error_code` と秘密値を含まない固定の `hint` を追加しました。
- 既存のCLIと方針を保ったままrouting実装を責務別モジュールへ分割し、Windows / Python 3.13をCI対象に追加しました。
- READMEを導入、適用確認、任意機能、トラブル対応の順に整理しました。

## 0.8.1

- PRレビューで、通常Chat向けbundleのシリアライズ後のサイズ境界を検査し、初回操作の権限確認と実行済み確認を区別する `permission_checked` を追加しました。

## 0.8.0

- 基準運用を親Terra/xhigh、子Luna/max・Sol/high・Astra/highの固定effortへ更新しました。Terra子は標準ルートから外し、利用者が選んだ親設定、既存override、他モデル親は維持します。
- 有効なroot経路では、能力と許可が合う通常Chat 6 Proをまとまった工程の最初の主担当候補にしました。並列分担には親の独立作業、直列の専門委譲には具体的な追加価値とホストの許可が必要です。

## 0.7.0

- 確認済み接続の操作・範囲・有効期限をローカル台帳に保持し、`chat_plan.py` が必要な項目だけを照合する方式を追加しました。
- `chat_transfer.py` の一回限りのローカルフォームで、準備済み本文と最終回答をBrowser内の変数のまま受け渡せるようにしました。フックからの起動・送信は行いません。

## 0.3.1

- 有効方針ごとに通常作業3件までを対象とする観測記録を追加しました。再開や子の仕事は同じ作業にまとめ、設定確認・監査は枠に含めません。
- プラグインの版だけが変わり、分担方針が同じ場合は同じ観測群を継続します。

## 0.2.0

- Windowsで、通常の起動方法を変えずに登録したGit refを15分ごとに確認する任意の自動更新を追加しました。
- プラグイン配布ファイルの変更時にmanifest versionの変更を要求するCI検査を追加しました。

## 0.1.0

- 標準サブエージェント向けの分担方針、部分上書き、競合診断、開始・再開時のフックを含む初版です。
- 初版から移行する場合も、Codexアプリ・CLIをすべて終了して `codex plugin marketplace upgrade codex-task-routing` を実行します。
