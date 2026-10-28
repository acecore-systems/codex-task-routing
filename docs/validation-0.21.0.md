# 0.21.0 の検証範囲

通常のCodex側実装・修正・デバッグ・探索・複数ファイル作業・調査下書きの既定委譲先をTerraに変更します。Solは合意済み設計の具体化や能力不足に理由を記して選び、許可・深さ条件が成立する場合にTerraへ実装を委譲し、成果をレビュー・統合します。Terraは方式選択・合意前提の変更を親へ返します。

標準孫の範囲はAstra→Sol/Terra/Luna、Sol→Terra/Luna、Terra→Lunaです。孫から先、Terra→Sol、同モデル・上位役割への委譲は不可。通常Chatはrootまたはroot直下Sol子だけが所有し、Cloud/Proの優先条件と既存担当の継続を維持します。モデルとeffort、旧terra互換、明示overrideは変更しません。

この変更は未マージの開発版です。稼働中の0.20.0では新しい役割境界は適用されません。既存の文章overrideは自動移行せず、矛盾があれば確認が必要です。ローカルのテストやフック生成は実際のモデル選定、性能・費用、Cloud/Chat送受信や稼働環境への導入の証明ではありません。

## 検証

2026-10-28、Windows・Python 3.13・ブランチ`codex/terra-default-delegation`、基点`05709cc266895db715601dae3f4d8395854878a5`の未コミット差分で確認しました。

- `python -m unittest discover -s tests -v`：通常権限のテスト用一時環境で306件成功、終了コード0。Windows sandboxでは一時ディレクトリのACLにより実行失敗したため、この結果には含めません。
- 設計判断の文面明確化後は関連49件（fixed 22件、routing 27件）を再実行して成功。上記306件はその文面修正前の全件結果です。
- `python scripts/check_package.py`：成功。manifest・参照・フック・既定・配布物の公開前チェックを実施。
- `git diff --check`：成功。
- 0.21用summary override候補と長さ154文字の隔離プラグインパスで両フックを実行し、Terra方針を含む親7970文字・子5874文字が8000文字の上限内であることを確認。実インストールと同一ではなく、別の長さやoverride全般を保証しません。

参照した[claude-task-routing](https://github.com/acecore-systems/claude-task-routing)の`agents/haiku-worker-xhigh.md`、`agents/sonnet-worker.md`、`policy/task-routing.md`の役割分担をTerra/Solへ対応させました。CLI子の起動、transcript読取、PreToolUse強制停止や失敗回数の強制は移植しません。
