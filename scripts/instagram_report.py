#!/usr/bin/env python3
"""Metricool APIからCASSELINIのInstagramデータを取得し、課題点レポートを生成する。

使い方:
    python scripts/instagram_report.py                # 直近90日を分析してレポート生成
    python scripts/instagram_report.py --days 30      # 対象期間を変更
    python scripts/instagram_report.py --list-brands  # blogId(ブランドID)の一覧を表示
    python scripts/instagram_report.py --demo         # サンプルデータでレポートの形式を確認

必要な環境変数(--demo / --list-brands以外):
    METRICOOL_API_TOKEN  MetricoolのSettings → APIで発行したトークン(Advancedプラン以上)
    METRICOOL_USER_ID    MetricoolのユーザーID
    METRICOOL_BLOG_ID    分析対象ブランドのID(--list-brandsで確認できる)
"""

import argparse
import csv
import glob
import json
import os
import shutil
import statistics
import sys
from datetime import datetime, timedelta, timezone

import requests

BASE_URL = os.environ.get("METRICOOL_BASE_URL", "https://app.metricool.com/api")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "reports", "instagram")
ASTREAM_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "astream")
JST = timezone(timedelta(hours=9))

# 課題判定のしきい値。アパレルEC系Instagramアカウントの一般的な水準をもとにした初期値なので、
# 運用しながらCASSELINIの実績に合わせて調整する。
THRESHOLDS = {
    "posts_per_week_low": 3.0,       # フィード+リール投稿がこれ未満なら「投稿頻度不足」
    "posts_per_week_critical": 1.5,
    "er_followers_low": 0.008,       # エンゲージメント率(対フォロワー)
    "er_followers_critical": 0.004,
    "er_reach_low": 0.03,            # エンゲージメント率(対リーチ)
    "follower_growth_low": 0.01,     # 期間中のフォロワー増加率
    "reels_share_low": 0.30,         # 全投稿に占めるリールの割合
    "save_rate_low": 0.008,          # 保存数 / リーチ
    "stories_per_week_low": 3.0,
    "link_click_rate_low": 0.05,     # サイトクリック / プロフィールアクセス
}


class MetricoolClient:
    """Metricool REST APIの薄いラッパー。

    認証はX-Mc-Authヘッダー。エンドポイントはMetricool公式APIドキュメント
    (https://app.metricool.com/resources/apidocs/index.html)に準拠。
    """

    def __init__(self, token, user_id):
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers["X-Mc-Auth"] = token

    def _get(self, path, **params):
        params.setdefault("userId", self.user_id)
        resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=60)
        if resp.status_code == 401:
            raise RuntimeError(
                "Metricool APIの認証に失敗しました(401)。METRICOOL_API_TOKENが正しいか、"
                "プランがAdvanced以上かを確認してください。"
            )
        resp.raise_for_status()
        return resp.json()

    def list_brands(self):
        return _extract_list(self._get("/admin/simpleProfiles"))

    def timeline(self, blog_id, metric, start, end):
        data = self._get(
            "/v2/analytics/timelines",
            blogId=blog_id,
            metric=metric,
            network="instagram",
            **{"from": _fmt(start), "to": _fmt(end)},
        )
        return _parse_timeline(data)

    def posts(self, blog_id, kind, start, end):
        """kind: 'posts' | 'reels' | 'stories'"""
        data = self._get(
            f"/v2/analytics/{kind}/instagram",
            blogId=blog_id,
            **{"from": _fmt(start), "to": _fmt(end)},
        )
        return _extract_list(data)


def _fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _extract_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "posts", "items", "results", "content"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _parse_timeline(data):
    """タイムラインAPIのレスポンスを[(日付文字列, 値), ...]に正規化する。"""
    points = []

    def walk(node):
        if isinstance(node, list):
            if len(node) == 2 and isinstance(node[1], (int, float)) and isinstance(node[0], str):
                points.append((node[0], node[1]))
            else:
                for child in node:
                    walk(child)
        elif isinstance(node, dict):
            date = node.get("date") or node.get("dateTime") or node.get("day")
            value = node.get("value", node.get("count"))
            if date is not None and isinstance(value, (int, float)):
                points.append((str(date), value))
            else:
                for child in node.values():
                    walk(child)

    walk(data)
    points.sort(key=lambda p: p[0])
    return points


def _num(item, *keys):
    """複数の候補キーから最初に見つかった数値を返す(レスポンス形式の揺れを吸収)。"""
    for key in keys:
        value = item.get(key)
        if isinstance(value, (int, float)):
            return value
    return 0


def collect_data(client, blog_id, start, end):
    """Metricoolから取得できたものだけを集め、失敗したAPIは警告として記録する。"""
    snapshot = {"warnings": []}

    for name, kind in [("posts", "posts"), ("reels", "reels"), ("stories", "stories")]:
        try:
            snapshot[name] = client.posts(blog_id, kind, start, end)
        except Exception as e:
            snapshot[name] = []
            snapshot["warnings"].append(f"{kind}の取得に失敗: {e}")

    try:
        snapshot["followers_timeline"] = client.timeline(blog_id, "followers", start, end)
    except Exception as e:
        snapshot["followers_timeline"] = []
        snapshot["warnings"].append(f"フォロワー推移の取得に失敗: {e}")

    return snapshot


def summarize_posts(items):
    """投稿リストからKPIを集計する。"""
    if not items:
        return None
    reach = [_num(p, "reach", "impressions", "views") for p in items]
    interactions = [
        _num(p, "interactions", "engagement")
        or (_num(p, "likes", "likesCount") + _num(p, "comments", "commentsCount") + _num(p, "saved", "saves") + _num(p, "shares"))
        for p in items
    ]
    saves = [_num(p, "saved", "saves") for p in items]
    return {
        "count": len(items),
        "total_reach": sum(reach),
        "avg_reach": statistics.mean(reach) if reach else 0,
        "total_interactions": sum(interactions),
        "avg_interactions": statistics.mean(interactions) if interactions else 0,
        "total_saves": sum(saves),
    }


def analyze(snapshot, days):
    """KPIを計算し、課題点(issues)を抽出する。"""
    weeks = days / 7.0
    posts = summarize_posts(snapshot["posts"])
    reels = summarize_posts(snapshot["reels"])
    stories = summarize_posts(snapshot["stories"])

    timeline = snapshot["followers_timeline"]
    followers_start = timeline[0][1] if timeline else None
    followers_end = timeline[-1][1] if timeline else None

    feed_reel_count = (posts["count"] if posts else 0) + (reels["count"] if reels else 0)
    total_reach = (posts["total_reach"] if posts else 0) + (reels["total_reach"] if reels else 0)
    total_interactions = (posts["total_interactions"] if posts else 0) + (reels["total_interactions"] if reels else 0)
    total_saves = (posts["total_saves"] if posts else 0) + (reels["total_saves"] if reels else 0)

    kpi = {
        "followers_start": followers_start,
        "followers_end": followers_end,
        "posts_per_week": feed_reel_count / weeks if weeks else 0,
        "stories_per_week": (stories["count"] if stories else 0) / weeks if weeks else 0,
        "avg_reach": total_reach / feed_reel_count if feed_reel_count else 0,
        "er_reach": total_interactions / total_reach if total_reach else None,
        "er_followers": (total_interactions / feed_reel_count / followers_end)
        if feed_reel_count and followers_end
        else None,
        "save_rate": total_saves / total_reach if total_reach else None,
        "reels_share": (reels["count"] / feed_reel_count) if reels and feed_reel_count else 0,
        "follower_growth": ((followers_end - followers_start) / followers_start)
        if followers_start
        else None,
        "posts": posts,
        "reels": reels,
        "stories": stories,
    }

    issues = []
    t = THRESHOLDS

    if kpi["posts_per_week"] < t["posts_per_week_critical"]:
        sev = "高"
    elif kpi["posts_per_week"] < t["posts_per_week_low"]:
        sev = "中"
    else:
        sev = None
    if sev:
        issues.append({
            "severity": sev,
            "title": "投稿頻度が不足している",
            "evidence": f"フィード+リールの投稿が週{kpi['posts_per_week']:.1f}本(推奨: 週{t['posts_per_week_low']:.0f}本以上)。",
            "action": "特集ページ更新・新作入荷・スタイリング提案を軸に、週3〜5本の投稿カレンダーを固定化する。既存のX Bot同様、特集ページRSSからの投稿ネタ連携も検討。",
        })

    if kpi["er_followers"] is not None:
        if kpi["er_followers"] < t["er_followers_critical"]:
            sev = "高"
        elif kpi["er_followers"] < t["er_followers_low"]:
            sev = "中"
        else:
            sev = None
        if sev:
            issues.append({
                "severity": sev,
                "title": "エンゲージメント率(対フォロワー)が低い",
                "evidence": f"平均{kpi['er_followers'] * 100:.2f}%(アパレルECの目安: 0.8%以上)。",
                "action": "保存されやすい実用コンテンツ(着回し・サイズ感・コーデ提案)を増やし、キャプション末尾で質問を投げてコメントを促す。",
            })

    if kpi["er_reach"] is not None and kpi["er_reach"] < t["er_reach_low"]:
        issues.append({
            "severity": "中",
            "title": "リーチに対する反応率が低い",
            "evidence": f"エンゲージメント率(対リーチ)が{kpi['er_reach'] * 100:.1f}%(目安: 3%以上)。届いてはいるが反応につながっていない。",
            "action": "1枚目の画像のフックを強化(価格・着用シーン・ビフォーアフター)。カルーセルで滞在時間を伸ばす。",
        })

    if kpi["follower_growth"] is not None:
        monthly_growth = kpi["follower_growth"] / (days / 30.0) if days else 0
        if kpi["follower_growth"] <= 0:
            issues.append({
                "severity": "高",
                "title": "フォロワーが減少している",
                "evidence": f"期間中のフォロワー増加率{kpi['follower_growth'] * 100:+.1f}%。",
                "action": "直近の投稿内容とフォロワー解除タイミングを突き合わせ、原因投稿を特定する。Astreamのフォロワー属性データでターゲットとのズレを確認。",
            })
        elif monthly_growth < t["follower_growth_low"]:
            issues.append({
                "severity": "中",
                "title": "フォロワーの伸びが停滞している",
                "evidence": f"期間中のフォロワー増加率{kpi['follower_growth'] * 100:+.1f}%(月換算{monthly_growth * 100:+.2f}%、目安: 月1%以上)。",
                "action": "リール・コラボ投稿・ハッシュタグ経由の新規リーチ獲得を強化する。",
            })

    if kpi["reels_share"] < t["reels_share_low"]:
        reach_note = ""
        if posts and reels and posts["avg_reach"] > 0 and reels["avg_reach"] > posts["avg_reach"]:
            reach_note = f"リールの平均リーチはフィードの{reels['avg_reach'] / posts['avg_reach']:.1f}倍あり、"
        issues.append({
            "severity": "中",
            "title": "リールの活用が不足している",
            "evidence": f"{reach_note}投稿に占めるリールの割合が{kpi['reels_share'] * 100:.0f}%(目安: 30%以上)。",
            "action": "新規リーチの主戦場はリール。着用動画・コーデ組み動画を週1〜2本から始める。",
        })

    if kpi["save_rate"] is not None and kpi["save_rate"] < t["save_rate_low"]:
        issues.append({
            "severity": "中",
            "title": "保存率が低い(あとで見返される投稿が少ない)",
            "evidence": f"保存数/リーチが{kpi['save_rate'] * 100:.2f}%(目安: 0.8%以上)。保存はEC訪問・購入の先行指標。",
            "action": "「保存して見返したくなる」情報型投稿(サイズ表・着回し1週間・お手入れ方法)を型として持つ。",
        })

    if kpi["stories_per_week"] < t["stories_per_week_low"]:
        issues.append({
            "severity": "低",
            "title": "ストーリーズの投稿が少ない",
            "evidence": f"週{kpi['stories_per_week']:.1f}本(目安: 週3本以上)。既存フォロワーとの接点維持とサイト送客(リンクスタンプ)の主要チャネル。",
            "action": "入荷情報・再入荷・セールはストーリーズ+リンクスタンプで即日発信する運用に。",
        })

    issues.append({
        "severity": "中",
        "title": "サイト送客の計測を確立する",
        "evidence": "Instagram→casselini-online.comへの流入がこのレポートではまだ計測できていない(プロフィールのリンククリック・ストーリーズのリンクタップ)。",
        "action": "プロフィールとストーリーズのリンクにUTMパラメータ(utm_source=instagram)を付与し、次回以降のレポートで送客数を追跡する。",
    })

    order = {"高": 0, "中": 1, "低": 2}
    issues.sort(key=lambda i: order[i["severity"]])
    return kpi, issues


def load_astream_csvs():
    """data/astream/ に置かれたAstreamのCSVエクスポートを要約する。"""
    summaries = []
    for path in sorted(glob.glob(os.path.join(ASTREAM_DIR, "*.csv"))):
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            summaries.append({
                "name": os.path.basename(path),
                "rows": max(len(rows) - 1, 0),
                "columns": rows[0] if rows else [],
            })
        except Exception as e:
            summaries.append({"name": os.path.basename(path), "error": str(e)})
    return summaries


def fmt_num(value, fmt="{:,.0f}"):
    return fmt.format(value) if value is not None else "—"


def fmt_pct(value, digits=2):
    return f"{value * 100:.{digits}f}%" if value is not None else "—"


def render_markdown(kpi, issues, astream, snapshot, start, end, demo=False):
    lines = []
    lines.append(f"# CASSELINI Instagram 分析レポート({end.strftime('%Y-%m-%d')})")
    lines.append("")
    if demo:
        lines.append("> ⚠️ これはサンプルデータによるデモレポートです。実データではありません。")
        lines.append("")
    lines.append(f"- 対象期間: {start.strftime('%Y-%m-%d')} 〜 {end.strftime('%Y-%m-%d')}")
    lines.append("- データソース: Metricool API(Instagram)" + ("、Astream CSVエクスポート" if astream else ""))
    lines.append("")

    lines.append("## KPIサマリー")
    lines.append("")
    lines.append("| 指標 | 値 |")
    lines.append("| --- | --- |")
    lines.append(f"| フォロワー数 | {fmt_num(kpi['followers_end'])}(期間開始時 {fmt_num(kpi['followers_start'])}) |")
    lines.append(f"| フォロワー増加率 | {fmt_pct(kpi['follower_growth'], 1)} |")
    lines.append(f"| 投稿頻度(フィード+リール) | 週{kpi['posts_per_week']:.1f}本 |")
    lines.append(f"| ストーリーズ頻度 | 週{kpi['stories_per_week']:.1f}本 |")
    lines.append(f"| 平均リーチ/投稿 | {fmt_num(kpi['avg_reach'])} |")
    lines.append(f"| エンゲージメント率(対リーチ) | {fmt_pct(kpi['er_reach'], 1)} |")
    lines.append(f"| エンゲージメント率(対フォロワー) | {fmt_pct(kpi['er_followers'])} |")
    lines.append(f"| 保存率(保存/リーチ) | {fmt_pct(kpi['save_rate'])} |")
    lines.append(f"| リール比率 | {fmt_pct(kpi['reels_share'], 0)} |")
    lines.append("")

    lines.append(f"## 課題点({len(issues)}件)")
    lines.append("")
    for i, issue in enumerate(issues, 1):
        lines.append(f"### {i}. [{issue['severity']}] {issue['title']}")
        lines.append("")
        lines.append(f"- **現状**: {issue['evidence']}")
        lines.append(f"- **改善アクション**: {issue['action']}")
        lines.append("")

    lines.append("## 投稿タイプ別パフォーマンス")
    lines.append("")
    lines.append("| タイプ | 投稿数 | 平均リーチ | 平均エンゲージメント |")
    lines.append("| --- | --- | --- | --- |")
    for label, key in [("フィード", "posts"), ("リール", "reels"), ("ストーリーズ", "stories")]:
        s = kpi[key]
        if s:
            lines.append(f"| {label} | {s['count']} | {fmt_num(s['avg_reach'])} | {fmt_num(s['avg_interactions'], '{:,.1f}')} |")
        else:
            lines.append(f"| {label} | — | — | — |")
    lines.append("")

    lines.append("## Astreamデータ(フォロワー分析)")
    lines.append("")
    if astream:
        for s in astream:
            if "error" in s:
                lines.append(f"- `{s['name']}`: 読み込みエラー({s['error']})")
            else:
                cols = ", ".join(s["columns"][:8]) + ("…" if len(s["columns"]) > 8 else "")
                lines.append(f"- `{s['name']}`: {s['rows']}行(列: {cols})")
        lines.append("")
    else:
        lines.append("AstreamはAPIを提供していないため、管理画面からCSVをエクスポートして `data/astream/` に置くと、このレポートに取り込まれます。")
        lines.append("")
    lines.append("Astreamで確認すべきポイント:")
    lines.append("")
    lines.append("- [ ] フォロワーの興味関心カテゴリ上位に「ファッション・通販」が入っているか")
    lines.append("- [ ] フォロワーの年齢・性別分布がCASSELINIのターゲット層と一致しているか")
    lines.append("- [ ] フォロワーが好むブランドと自社の世界観にズレがないか")
    lines.append("")

    if snapshot.get("warnings"):
        lines.append("## データ取得の警告")
        lines.append("")
        for w in snapshot["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("---")
    lines.append(f"_このレポートは `scripts/instagram_report.py` により自動生成されました({datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')})_")
    lines.append("")
    return "\n".join(lines)


def demo_snapshot():
    """レポート形式を確認するためのサンプルデータ(実データではない)。"""
    posts = [
        {"reach": r, "likes": l, "comments": c, "saved": s}
        for r, l, c, s in [
            (2100, 68, 2, 9), (1850, 55, 1, 6), (3200, 110, 5, 18), (1600, 42, 0, 4),
            (2400, 75, 3, 11), (1900, 58, 2, 7), (2800, 95, 4, 15), (1700, 48, 1, 5),
            (2200, 70, 2, 10), (2000, 60, 1, 8), (2600, 85, 3, 12), (1500, 40, 1, 3),
            (2300, 72, 2, 9), (1800, 52, 1, 6), (2500, 80, 3, 13), (1950, 62, 2, 8),
            (2050, 65, 2, 7), (1750, 50, 1, 5),
        ]
    ]
    reels = [
        {"reach": r, "likes": l, "comments": c, "saved": s}
        for r, l, c, s in [
            (5800, 145, 6, 28), (4200, 105, 3, 19), (7500, 190, 8, 41),
            (3900, 92, 2, 15), (5100, 128, 5, 24), (4600, 112, 4, 20),
        ]
    ]
    stories = [{"reach": 900 + i * 15, "interactions": 12 + i % 5} for i in range(22)]
    followers = [
        (f"2026-{m:02d}-{d:02d}", v)
        for (m, d), v in zip(
            [(4, 13), (4, 27), (5, 11), (5, 25), (6, 8), (6, 22), (7, 6), (7, 12)],
            [12400, 12420, 12435, 12455, 12470, 12500, 12520, 12530],
        )
    ]
    return {"posts": posts, "reels": reels, "stories": stories, "followers_timeline": followers, "warnings": []}


def write_report(markdown, snapshot, end, demo=False):
    os.makedirs(REPORT_DIR, exist_ok=True)
    if demo:
        path = os.path.join(REPORT_DIR, "sample-report.md")
    else:
        path = os.path.join(REPORT_DIR, f"{end.strftime('%Y-%m-%d')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"report written: {os.path.relpath(path)}")

    if not demo:
        shutil.copyfile(path, os.path.join(REPORT_DIR, "latest.md"))
        snap_dir = os.path.join(REPORT_DIR, "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap_path = os.path.join(snap_dir, f"{end.strftime('%Y-%m-%d')}.json")
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"snapshot written: {os.path.relpath(snap_path)}")


def require_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(
            f"環境変数が未設定です: {', '.join(missing)}。"
            "設定方法は docs/setup-instagram-analytics.md を参照してください。"
        )
    return [os.environ[n] for n in names]


def main():
    parser = argparse.ArgumentParser(description="CASSELINI Instagram 分析レポート生成")
    parser.add_argument("--days", type=int, default=90, help="分析対象期間(日数、デフォルト90)")
    parser.add_argument("--list-brands", action="store_true", help="Metricoolのブランド一覧(blogId)を表示")
    parser.add_argument("--demo", action="store_true", help="サンプルデータでレポートを生成")
    args = parser.parse_args()

    end = datetime.now(JST)
    start = end - timedelta(days=args.days)

    if args.demo:
        snapshot = demo_snapshot()
    elif args.list_brands:
        token, user_id = require_env("METRICOOL_API_TOKEN", "METRICOOL_USER_ID")
        client = MetricoolClient(token, user_id)
        brands = client.list_brands()
        if not brands:
            print("ブランドが見つかりませんでした。MetricoolにInstagramアカウントを接続してください。")
            return
        for b in brands:
            label = b.get("label") or b.get("title") or b.get("name") or "?"
            blog_id = b.get("blogId") or b.get("id") or "?"
            print(f"blogId={blog_id}  {label}")
        return
    else:
        token, user_id, blog_id = require_env(
            "METRICOOL_API_TOKEN", "METRICOOL_USER_ID", "METRICOOL_BLOG_ID"
        )
        client = MetricoolClient(token, user_id)
        snapshot = collect_data(client, blog_id, start, end)
        if not any(snapshot[k] for k in ("posts", "reels", "stories", "followers_timeline")):
            raise RuntimeError(
                "Metricoolからデータを取得できませんでした。警告: "
                + " / ".join(snapshot["warnings"])
            )

    kpi, issues = analyze(snapshot, args.days)
    astream = load_astream_csvs()
    markdown = render_markdown(kpi, issues, astream, snapshot, start, end, demo=args.demo)
    write_report(markdown, snapshot, end, demo=args.demo)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
