---
description: github_notify MCPツールの使い方ガイド
---
# github_notify

## 用途
Check GitHub repos for new events and send Discord notifications.

## 引数
なし（設定ファイルから読み取り）

## 使用場面
- GitHubリポジトリの新しいイベント（push, PR, issue等）をDiscordに通知する時
- 定期的なリポジトリ監視（cronジョブと併用）

## 使用すべきでない場面
- 設定ファイル（discord_data/github_notifier_config.json）が未作成の時
- GitHub tokenが未設定の時
- discord_connectが未完了の状態

## 設定方法

### 1. 設定ファイル作成
`discord_data/github_notifier_config.json`:
```json
{
  "github_token": "ghp_your_token_here",
  "repositories": ["owner/repo1", "owner/repo2"],
  "check_interval_seconds": 300,
  "last_check": {},
  "event_types": ["PushEvent", "PullRequestEvent", "IssuesEvent", "CreateEvent"]
}
```

注意: `github_token`はファイル保存時に自動除去される（セキュリティ）。
トークンは毎回手動で設定するか、環境変数 `GITHUB_TOKEN` を使用する。

### 2. GitHub Token取得
- GitHub Settings → Developer settings → Personal access tokens
- 必要なスコープ: `repo`（privateリポの場合）または `public_repo`

### 3. cronジョブ登録（定期実行）
```
persistent_cron_add:
  name: github-notify
  schedule: "*/5 * * * *"
  command: "github_notify"
  description: "5分毎にGitHubイベントをチェックしてDiscord通知"
```

## 対応イベントタイプ
- `PushEvent`: プッシュ（コミット）
- `PullRequestEvent`: PR作成/更新/クローズ
- `IssuesEvent`: Issue作成/更新/クローズ
- `CreateEvent`: ブランチ/タグ作成

## 戻り値
- `"N new events notified"`: N件の新イベントを通知
- `"No new events"`: 新しいイベントなし
- `"Not configured: ..."`: 設定不足のエラーメッセージ

## 関連ツール
- discord_connect（Discord接続が必要）
- discord_send（内部で使用）
- persistent_cron_add（定期実行の設定）

## 制約・注意点
- GitHub APIレート制限: 認証なし60/h、認証あり5000/h
- Hook guard: guard-github-notify（rate_limit: 5分に1回まで）
- discord_connect完了後にのみDiscord送信が動作
- last_checkは設定ファイルに自動保存（重複通知防止）

## 1:1:1対応
- Hook: guard-github-notify (behavior-rules.json)
- Skill: tool-github-notify.md (このファイル)
- MCP: github_notify (self_observation_mcp_server.py)
