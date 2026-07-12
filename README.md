# casselini-x-bot

CASSELINI公式オンラインショップの特集ページ(`https://www.casselini-online.com/feature/`)のRSSフィードを監視し、新着があれば自動でXに投稿するBotです。GitHub Actionsで30分おきに実行されます。

このリポジトリには、公式Instagramの課題点を毎週自動抽出する分析レポート機能も含まれています(下記「Instagram分析レポート」参照)。

## 仕組み

1. `https://www.casselini-online.com/feature/feed/` を取得
2. `data/known.json` に記録済みのURL一覧と比較し、新着記事を検出
3. 新着があればタイトル+URLでツイートを作成し、X API v2 (`POST /2/tweets`) で投稿
4. 投稿後、`data/known.json` を更新してリポジトリにコミット

初回実行時は既存の特集ページを一括投稿しないよう、現在のフィード内容をそのまま「既知」として記録するだけで投稿は行いません。2回目以降の実行で新着が出たときから投稿が始まります。

## セットアップ

### 1. X Developer PortalでAppを作成

投稿先のXアカウント(例: @CASSELINI_JP)でログインした状態で [developer.x.com](https://developer.x.com/) からAppを作成してください。

- App permissions は **Read and Write** に設定する(投稿にはWrite権限が必須)
- 認証方式は OAuth 1.0a を使用
- 以下の4つの値を取得する
  - API Key (Consumer Key)
  - API Key Secret (Consumer Secret)
  - Access Token
  - Access Token Secret

Access Token / Secret は、投稿したいXアカウントでログインした状態で発行する必要があります。開発者アカウントの持ち主と投稿先アカウントが違う場合は、投稿先アカウントでのログインが必要になるので注意してください。

### 2. GitHub Secretsに登録

このリポジトリの Settings → Secrets and variables → Actions に以下を登録:

- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_SECRET`

### 3. 動作確認

`Actions` タブから `Post CASSELINI features to X` ワークフローを `Run workflow` で手動実行できます。初回は投稿されず `data/known.json` が作成されるだけなので、まずはこれで動作確認してください。

## ローカルでの動作確認

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/post_features.py
```

初回はAPIキーなしでも実行できます(投稿処理に到達しないため)。2回目以降に新着があり投稿処理が走る場合は、上記4つの環境変数をエクスポートしてから実行してください。

## 投稿頻度・文言を変えたい場合

- 実行頻度: `.github/workflows/post-features.yml` の `cron` を変更
- ツイート文言: `scripts/post_features.py` の `build_tweet_text` を編集

## Instagram分析レポート

Metricool API(+Astream CSVエクスポート)からInstagramのデータを取得し、KPIと課題点をまとめたMarkdownレポートを毎週月曜9:00(JST)に `data/reports/instagram/` へ自動コミットします。

- セットアップ手順: [docs/setup-instagram-analytics.md](docs/setup-instagram-analytics.md)
- レポートのサンプル: [data/reports/instagram/sample-report.md](data/reports/instagram/sample-report.md)
- 必要なSecrets: `METRICOOL_API_TOKEN` / `METRICOOL_USER_ID` / `METRICOOL_BLOG_ID`(Advancedプラン以上が必要)
