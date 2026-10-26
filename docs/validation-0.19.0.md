# 0.19.0 の検証範囲

2026-10-23、専用ブランチ`codex/cloud-first-repository-routing`、基点`7b8e79c`（0.18.1）で実施しました。

## 方針の意味変更

- 新規の独立repo実装・検証は、材料・環境・テスト・ホスト条件が適合すればCloudをProより先に選びます。同じ完了単位の適任担当、短い密結合の仕事、同一目的の限定補助は従来の継続・標準子条件を維持します。Cloudに不適合な工程やrepo以外の分析等は有効なProを引き続き先に検討します。
- repo環境の未確認・不存在・既存を分けます。未確認は調査、不存在確認後は対応UIで作成・準備・テスト・公開、既存はrepoと公開/準備状態を照合して再利用します。`ready:true`は今回必要なrepo/revision・依存・テスト・ネットワークまで利用できると確認した状態です。
- 環境とtaskを区別し、独立目的には既存環境から新セッション、同じ完了単位には元担当の継続を計画します。同一repoの複数セッションは独立範囲・分離branch/workspace・統合担当を指定し、衝突を調整します。委譲者の独立作業とホストの並列許可・上限は維持します。
- `recommend`のschema 1へ任意の`work.repository`、`execution.cloud.environment`、`execution.cloud.parallel`を追加しました。旧入力も受理し、環境未指定はunknownとして調査へ進みます。推薦は常に`execution_authorized:false`です。`cloud_lifecycle`は条件付きの準備順序で、実行候補はtop-level `next_action`だけです。
- defaults、注入文、分類/引継ぎtemplate、SKILL、参照手順、README、生成fixtures/provenanceを同期しました。配布だけの修正ではなく、優先順の意味変更としてminor版を上げています。

## 検証結果

Windows / Python 3.13.15。

| 検証 | 結果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 302件成功、skipなし、終了0（10.303秒）。recommendは30件 |
| 推薦境界 | Cloud/Pro双方が可能なrepoはCloud、分析はPro、不適合Cloudの代案、同一担当継続、不明環境、確認済み不存在、既存準備不足/不明、repo不一致、setup未対応、並列衝突、所有者/ホスト不足の優先を確認 |
| `python scripts/check_package.py` | 成功。manifest、参照、フック、defaults、個人パス・秘密値・未完成素材の公開物検査 |
| `git diff --check` | 成功 |
| 既定値と生成文書 | models全体とpolicy_local_authが基点と一致。3生成fixtureのSHA-256がprovenanceと一致 |
| フック | 一時Codexホームで両フックのCloud優先文と通常Chatの条件、長い導入先での8000字上限を既存回帰テストで確認 |

最初の制限トークンでの全件実行は、既存のprivate temp・atomic replace・hardlinkのWindows ACL制約と未同期fixtureで失敗しました。fixtureは継承ACLの隔離フォルダーで再生成し、通常ユーザー権限で全302件を確認しました。ユーザーの利用環境へインストールせず、設定・認証情報・AGENTSは変更していません。検証用の出力はGit対象外です。

## 実接続の範囲と未検証（2026-10-23時点）

以下は当時の検証範囲です。後日の追加実証は次節に分けて記録します。

この版では新規Cloud UIの環境作成・公開・task作成や、実Cloudでの0.19.0テスト、通常Chat送受信を行っていません。以前に保持されたCloud checkoutは0.15.2とネットワーク無効の状態のため、今回のローカル結果や以前の既存task継続成功を0.19.0の実Cloud検証に読み替えません。

環境作成・公開の対応UI手順は[公式資料](https://learn.chatgpt.com/docs/environments/cloud-environments)と照合しました（2026-10-23）。実際の機能・対象repoへのアクセス・今回の操作承認・公開完了・新taskのrevisionとテスト証拠は実操作時に別途確認します。

既存UI schema 1/保存schema 2、既存Codex durable taskのnative schema 2/保存schema 3、永久claimと再送防止は変更していません。新規native作成は未対応のままで、Work・旧CLI・simulated environmentへの代替、hookからの送信は追加していません。親設定・モデル/effort・明示override・認証境界も維持します。commit後のrelease検査・CI・PR・公開は親担当がそれぞれ実結果を記録します。


## 2026-10-27のCodex Cloud追加検証

対象は`acecore-systems/codex-task-routing`の0.19.0、実checkoutのHEADは`18bde3e6f6f1b52c8f9f6b475f85a0826039de8d`です。既存の公開済みrepo環境から新規Codex UIセッションを作成し、その同一セッションをnative経路で継続しました。UI作成と正式なread/waitによる結果回収は親担当の確認情報、継続依頼の受信・以下の環境検査とコマンド結果はCloud担当の直接観測です。

README・AGENTS・cloud-environment runtimeスキルとローカルネットワーク方針を確認しました。HTTP許可先とTCP許可先は空、VPN設定はなく、ネットワーク・インストール・認証情報やユーザー設定の読取りは行っていません。

| 検証 | 結果 |
| --- | --- |
| 開始・終了状態 | branch `work`、HEADは上記期待値と一致、git statusは両方clean |
| 実行環境 | Codex Cloud / Python 3.12.14、Python標準ライブラリのみ |
| `python -m unittest discover -s tests -v` | 総数302、成功298、失敗0、エラー0、Windows固有4skip、終了0（4.575秒） |
| skipの範囲 | Windowsのパス同一性、renameエラー対応、作業ディレクトリ長制限、ディレクトリ検索 |
| `python scripts/check_package.py` | 成功、終了0 |
| cleanup | 今回専用の一時領域とログを対象確認後に削除。既存fixture領域は空のまま保持 |

最初は`/tmp/.git`が存在する環境でTMPDIR/TEMP/TMPを`/tmp`配下へ隔離したため、claim保存先の祖先にGit管理領域があることを拒否する`private_storage_required`で22エラーになりました（成功276、skip 4）。製品コードの修正や既存`.git`の削除は行っていません。

次に`/dev/shm`への追加write rootを指定した実行では、通常の読取り時にはなかった`/dev/shm/.git`が見えたため、事前検査で停止しました。追加filesystem permission・write rootを指定しない既定権限の正式execでは、一時領域を作成できました。同じ単一shell内で作成先と祖先の`.git`・リンク不在、十分な空き容量を確認し、`/dev/shm`配下の今回専用一時領域にTMPDIR/TEMP/TMPと隔離Codexホームを指定して両コマンドを実行すると、上表の結果になりました。これはこの環境での観測であり、他環境でも同じ権限・一時領域が使える保証ではありません。

今回実証したのは既存repo環境での新規UIセッション、同一セッションのnative継続・read/wait回収、および当該HEADのCloudテストです。環境未作成repoのUIによる環境作成・公開は未実証です。通常Chat送受信、他OS、モデル/effortの実反映も今回の成功から推定しません。

## 同一repoのCloud並列作業と統合

2026-10-27（JST）、同じ公開済みrepo環境を再利用した2つのCodex Cloudセッションで、独立した文書変更を実行しました。基点は両方とも上記HEAD、開始時はcleanです。親は受入基準・ローカル統合・検証を担当しました。

| 担当 | 専用branch | 変更範囲 | 実作業時刻（UTC） |
| --- | --- | --- | --- |
| A | `codex/cloud-runtime-evidence` | `docs/validation-0.19.0.md` | 2026-10-26 16:06:23–16:07:23 |
| B | `codex/cloud-parallel-workflow` | `docs/cloud-session-workflow.md` | 2026-10-26 16:06:01–16:08:23 |

実作業の重複は60秒です。両方とも自身のbranchだけでcommitし、他担当のbranchは各ローカルGitに存在しませんでした。同じcheckoutパス名でも共有作業領域とは扱わず、別セッションで各branch・変更ファイル・終了HEAD・clean状態を回収しました。両担当のpackage検査とdiff検査が成功し、親が返却diffを専用統合branchに衝突なく適用して内容を確認しました。

これは独立した2文書での分離・並列実行・差分回収とローカル統合の実証です。同一ファイルの競合解決、共有外部サービスへの同時書込み、任意の並列数を保証するものではありません。最終PR・CI・mergeの結果は当該PRで確認します。
