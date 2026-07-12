# Instagram分析レポートのセットアップ

CASSELINI公式Instagramの課題点を自動抽出し、毎週月曜9:00(JST)にMarkdownレポートを `data/reports/instagram/` に自動コミットする仕組みのセットアップ手順です。

## 全体像

```
Metricool API ──┐
                ├─→ scripts/instagram_report.py ─→ data/reports/instagram/YYYY-MM-DD.md
Astream CSV ────┘        (GitHub Actionsで週次実行)      (課題点 + KPI + 改善アクション)
```

- **Metricool**: API経由でフォロワー推移・投稿別パフォーマンス(フィード/リール/ストーリーズ)を自動取得
- **Astream**: 公開APIが提供されていないため、管理画面からのCSVエクスポートを手動で取り込み(後述)

## 1. Metricool APIトークンの取得

> **重要**: Metricool APIは **Advancedプラン以上** でのみ利用できます。Free/Starterプランでは使えません。

1. [Metricool](https://app.metricool.com/) にログインし、CASSELINIのInstagramアカウントをブランドに接続する(未接続の場合)
   - Instagram側は**プロアカウント(ビジネス/クリエイター)**である必要があります
2. **Settings(設定)→ API** を開く
3. **APIトークンを生成**してコピーする
4. 同じ画面に表示される **User ID** を控える

公式ドキュメント: [Metricool API docs](https://app.metricool.com/resources/apidocs/index.html) / [API Access ガイド](https://help.metricool.com/api-access-export-your-metricool-data-to-other-tools-and-automate-tasks-x8ln5)

## 2. GitHub Secretsに登録

このリポジトリの **Settings → Secrets and variables → Actions** に以下を登録:

| Secret名 | 値 |
| --- | --- |
| `METRICOOL_API_TOKEN` | 手順1で発行したAPIトークン |
| `METRICOOL_USER_ID` | MetricoolのUser ID |
| `METRICOOL_BLOG_ID` | 分析対象ブランドのID(手順3で確認) |

## 3. blogId(ブランドID)の確認

`METRICOOL_API_TOKEN` と `METRICOOL_USER_ID` を登録したあと:

1. **Actions** タブ → **Instagram analytics report** → **Run workflow**
2. 「blogId確認モードで実行する」に**チェックを入れて**実行
3. ログに `blogId=12345 CASSELINI` のような一覧が表示されるので、対象ブランドのblogIdを `METRICOOL_BLOG_ID` としてSecretsに登録

## 4. 動作確認

**Run workflow** をチェックなしで実行すると、レポートが生成されて `data/reports/instagram/` にコミットされます。以降は毎週月曜9:00(JST)に自動実行されます。

レポートの形式は、サンプルデータで生成した [`data/reports/instagram/sample-report.md`](../data/reports/instagram/sample-report.md) で確認できます。

## 5. Astreamデータの取り込み(任意)

Astream(<https://astream.jp/>)は外部連携用のAPIを提供していないため、CSVエクスポートで取り込みます:

1. Astreamの管理画面でCASSELINIアカウントのフォロワー分析(興味関心・属性など)をCSVエクスポート
2. CSVファイルを `data/astream/` に置いてコミット
3. 次回のレポート生成時に、CSVの内容がレポートの「Astreamデータ」セクションに反映される

月1回程度の更新で十分です。

## レポートに含まれる内容

- **KPIサマリー**: フォロワー数・増加率、投稿頻度、平均リーチ、エンゲージメント率、保存率、リール比率
- **課題点**: しきい値ベースで自動抽出(重要度: 高/中/低)。根拠となる数値と改善アクション付き
- **投稿タイプ別パフォーマンス**: フィード/リール/ストーリーズの比較
- **Astreamチェックリスト**: フォロワー属性とECターゲットの一致度確認

## 使用しているAPIエンドポイント

実装は、オープンソースの [Metricool CLI](https://github.com/Purple-Horizons/metricool-cli) で実際に使われているパスに合わせています。

| データ | エンドポイント | 日付形式 |
| --- | --- | --- |
| ブランド一覧 | `GET /admin/simpleProfiles` | — |
| フィード投稿 | `GET /v2/analytics/posts/instagram` | ISO8601(`from`/`to`) |
| リール | `GET /v2/analytics/reels/instagram` | ISO8601(`from`/`to`) |
| ストーリーズ | `GET /stats/instagram/stories` | YYYYMMDD(`start`/`end`) |
| フォロワー推移 | `GET /stats/timeline/{metric}` | YYYYMMDD(`start`/`end`) |

認証は全リクエストで `X-Mc-Auth` ヘッダー + `userToken`/`userId` クエリを付与。すべての分析系は `blogId` が必須です。

## トラブルシューティング

- **フォロワー数が「—」になる**: `/stats/timeline/{metric}` のメトリクス名がMetricool側の呼称と異なる可能性があります。Secretに `METRICOOL_FOLLOWERS_METRIC` を追加して調整してください(デフォルト `Followers`)。ワークフローのログに、試行したメトリクス名を含む警告が出ます。
- **投稿の集計時刻がずれる**: `METRICOOL_TIMEZONE` をSecretに追加(デフォルト `Asia/Tokyo`)。
- **401エラー**: トークンが誤っているか、プランがAdvanced未満です。Settings → API で再発行してください。
- **一部データだけ取れない**: 取得に失敗したAPIはレポート末尾の「データ取得の警告」に記録され、取れたデータだけでレポートは生成されます。

### 任意で追加できるSecret

| Secret名 | 用途 | デフォルト |
| --- | --- | --- |
| `METRICOOL_FOLLOWERS_METRIC` | フォロワー推移のメトリクス名 | `Followers` |
| `METRICOOL_TIMEZONE` | 投稿集計のタイムゾーン | `Asia/Tokyo` |

## 課題判定のしきい値を調整したい場合

`scripts/instagram_report.py` の `THRESHOLDS` を編集してください。初期値はアパレルEC系アカウントの一般的な水準です。

## ローカルでの動作確認

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# サンプルデータでレポート形式を確認(APIキー不要)
python scripts/instagram_report.py --demo

# 実データで実行
export METRICOOL_API_TOKEN=...
export METRICOOL_USER_ID=...
export METRICOOL_BLOG_ID=...
python scripts/instagram_report.py
```

## 補足: Metricool Advancedプランにしない場合の代替案

Instagram公式の [Instagram Graph API](https://developers.facebook.com/docs/instagram-api/)(無料)から直接インサイトを取得する方法もあります。Meta開発者アプリの作成・Facebookページとの連携・長期アクセストークンの管理が必要になり、セットアップの手間は増えますが、ランニングコストはかかりません。切り替えたい場合は `scripts/instagram_report.py` のデータ取得部分の差し替えで対応できます。
