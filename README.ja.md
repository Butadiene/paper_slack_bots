<!--
  Slack ArXiv & RSS 通知ボットの README 日本語版
-->
# Slack ArXiv & RSS 通知ボット

このボットは新しい arXiv 投稿と RSS ジャーナルフィードを取得し、OpenAI で要約してSlack チャンネルに通知を投稿します。複数の Slack ワークスペースをサポートし、arXiv カテゴリ、キーワード、RSS ジャーナルをワークスペースごとに設定できます。

## 特徴
- 複数ワークスペース対応
- 設定可能な arXiv カテゴリ & キーワード
- 設定可能な RSS ジャーナルフィード
- 要約（OpenAI）
- 古いボットメッセージの自動削除（120～140 日）
- `days_back` に基づくスケジュール取得

## 要件
- Python 3.10 以上（3.11 でテスト済み）
- Git
- OpenAI API キー（使用するモデルへのアクセス権が必要）
- 各ワークスペース用の Slack ボットトークン
  
## Slack アプリのセットアップ
Slack API トークンを取得するには:
1. https://api.slack.com/apps にアクセスし、"Create New App" をクリックして「From scratch」を選択し、アプリ名とワークスペースを設定します。
2. 「OAuth & Permissions」ページで Bot Token Scopes に chat:write, channels:read, channels:history を追加します。
3. 「Install to Workspace」をクリックしてアプリをワークスペースにインストールし、承認します。
4. 発行された Bot User OAuth Token (xoxb-...) をコピーし、`secrets.yaml` の `slack_api_token` または `slack_api_tokens` に設定します。

### Slack チャンネルIDの取得方法

`slack_channel_id` を設定するためにチャンネルIDが必要です:
1. Slack デスクトップアプリやブラウザで目的のチャンネルを開きます。
2. 画面上部のチャンネル名をクリックし、チャンネル詳細を開きます。
3. 「リンクをコピー」を選択し、コピーしたURLの `/archives/` の後の文字列（例: `C01234567`）がチャンネルIDです。
4. （任意）Slack APIの `conversations.list` メソッドでチャンネル一覧とIDを取得できます。

## インストール
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 設定

### 1. config.yaml

グローバルなスケジュールと一つ以上のワークスペースを定義します:

```yaml
# 遡る日数（arXiv と RSS）
days_back: 5

# ワークスペースごとの設定
workspaces:
  - name: default          # secrets.yaml のキーに対応
    arxiv:
      slack_channel_id: YOUR_CHANNEL_ID
      categories:
        - astro-ph.EP
        - physics.space-ph
      keywords:
        - Alfven
    journals:
      - title: Journal-geophysical-space-physics
        rss_url: https://agupubs.onlinelibrary.wiley.com/action/showFeed?jc=21699402&type=etoc&feed=rss
        link_tag: link
        abstract_tag: content
        slack_channel_id: YOUR_JOURNAL_CHANNEL_ID

  - name: other_workspace   # 必要に応じて追加
    arxiv:
      slack_channel_id: YOUR_CHANNEL_ID
      categories: [your.category1, your.category2]
      keywords:   [keyword1, keyword2]
    journals:
      - title: Your-Journal
        rss_url: https://example.com/rss
        link_tag: link
        abstract_tag: content
        slack_channel_id: YOUR_JOURNAL_CHANNEL_ID
```

### 2. secrets.yaml

`secrets_template.yaml` を `secrets.yaml` としてコピーし、以下を設定します:

```yaml
openai_api_key: "<your-openai-api-key>"
openai_model:  "gpt-4o-mini"

# 単一ワークスペース向けのフォールバック（任意）
slack_api_token: "<your-slack-bot-token>"

# 基本: ワークスペース名とトークンのマッピング
slack_api_tokens:
  default:          "xoxb-..."
  other_workspace:  "xoxb-..."
```
### 環境変数

デフォルトのファイルパスは以下の環境変数で上書きできます:

- `CONFIG_FILE`: 設定ファイルのパス (デフォルト: `config.yaml`)
- `SECRETS_FILE`: シークレットファイルのパス (デフォルト: `secrets.yaml`)

## 使い方

単発実行またはスケジュール（cron ジョブなど）で実行します:

```bash
python main.py
```

スクリプトは以下を実行します:
1. `config.yaml` の各ワークスペースを処理
2. ワークスペースごとに Slack WebClient を初期化
3. arXiv から新しい論文を取得して投稿（カテゴリ & キーワードでフィルタリング）
4. 設定した各ジャーナルの RSS エントリーを取得して投稿
5. 120 日以上前のボットメッセージを自動削除（整理のため）

## 自動化 (GitHub Actions)

自動的に毎日実行するための GitHub Actions ワークフローを `.github/workflows/post_papers.yml` に用意しています。

### ワークフローの詳細
- スケジュール: cronで UTC 00:00 (JST 09:00) に毎日実行可能（デフォルトではコメントアウトされています）。
  自動実行を有効にするには、`.github/workflows/post_papers.yml` の `schedule` セクションのコメントを外してください。
- 手動トリガー: "Run workflow" ボタンから手動実行できます。
- 手順:
  1. リポジトリをチェックアウト
  2. Python 3.12 をセットアップ
  3. 依存関係をインストール
  4. GitHub リポジトリシークレットから `secrets.yaml` を生成
  5. `python main.py` を実行

### GitHub リポジトリシークレット (環境変数)

GitHub Actions では、リポジトリシークレットを環境変数として利用します（`${{ secrets.<NAME> }}`）。
リポジトリの **Settings > Secrets > Actions** で以下を設定してください:

- `OPENAI_API_KEY`: OpenAI API キー
- `SLACK_API_TOKEN`: `config.yaml` で定義した各ワークスペースの Slack Bot トークン。 
- 追加のワークスペースを `config.yaml` に定義した場合は、対応するシークレットを追加し、`.github/workflows/post_papers.yml` 内の `slack_api_tokens` マッピングを更新してください。
- `other_workspace`を参考にしてください。
