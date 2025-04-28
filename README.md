<!--
  README for Slack ArXiv & RSS Notification Bot
-->
# Slack ArXiv & RSS Notification Bot
[日本語版 README](README.ja.md)

This bot fetches new arXiv submissions and RSS journal feeds, summarizes abstracts via OpenAI and posts notifications to Slack channels. It supports multiple Slack
workspaces with independent configurations for arXiv categories, keywords, and RSS journals.

## Features
- Multi-workspace support
- Configurable arXiv categories & keywords
- Configurable RSS journal feeds
- Abstract summarization (OpenAI)
- Automatic pruning of old bot messages (120–140 days old)
- Scheduled fetch based on `days_back`

## Requirements
- Python 3.10+ (tested on 3.11)
- Git
- An OpenAI API key (with access to your chosen model)
- Slack bot tokens for each workspace
  
## Slack App Setup
To create a Slack App and obtain a bot token:
1. Visit https://api.slack.com/apps and click "Create New App". Choose "From scratch", give a name and select your workspace.
2. In "OAuth & Permissions", add Bot Token Scopes: chat:write, channels:read, channels:history.
3. Click "Install to Workspace" and authorize the app.
4. Copy the "Bot User OAuth Token" (starts with xoxb-) and add it to `secrets.yaml` under `slack_api_token` or in `slack_api_tokens` mapping.

### Obtaining a Slack Channel ID

To configure the `slack_channel_id` in your `config.yaml`, you need the Slack channel ID:
1. In Slack (desktop or browser), open the target channel.
2. Click the channel name at the top to open channel details.
3. Click “Copy link” – the copied URL contains the channel ID after `/archives/` (e.g., `C01234567`).
4. (Optional) Use the Slack API method `conversations.list` with your bot token to list channels and their IDs.

Install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

### 1. config.yaml

Define global schedule and one or more `workspaces`:

```yaml
# How many days back to look for new items (arXiv and RSS)
days_back: 5

# One entry per Slack workspace
workspaces:
  - name: default          # identifier matching secrets.yaml token
    arxiv:
      slack_channel_id: C020HPN7Z5M
      categories:
        - astro-ph.EP
        - physics.space-ph
      keywords:
        - Alfven
        - ULF
    journals:
      - title: Journal-geophysical-space-physics
        rss_url: https://agupubs.onlinelibrary.wiley.com/action/showFeed?jc=21699402&type=etoc&feed=rss
        link_tag: link
        abstract_tag: content
        slack_channel_id: C054WRABX7A

  - name: other_workspace   # add as many as needed
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

Copy `secrets_template.yaml` to `secrets.yaml` and fill in:

```yaml
openai_api_key: "<your-openai-api-key>"
openai_model:  "gpt-4o-mini"

# Optional single-workspace fallback
slack_api_token: "<your-slack-bot-token>"

# Primary: mapping of workspace names to bot tokens
slack_api_tokens:
  default:          "xoxb-..."
  other_workspace:  "xoxb-..."
```
### Environment Variables

You can override the default file paths by setting the following environment variables:

- `CONFIG_FILE`: Path to the configuration file (default: `config.yaml`)
- `SECRETS_FILE`: Path to the secrets file (default: `secrets.yaml`)

## Usage

Run one-off or via scheduler (cron/job):

```bash
python main.py
```

The script will:
1. Loop through each workspace in `config.yaml`.
2. Initialize a Slack WebClient for that workspace.
3. Fetch & post new arXiv papers (filtered by categories & keywords).
4. Fetch & post new RSS entries for each configured journal.
5. Prune bot messages older than 120 days (to avoid clutter).

## Automation (GitHub Actions)

A GitHub Actions workflow is provided in `.github/workflows/post_papers.yml`. It supports manual triggers and can be configured for daily scheduled runs.

Workflow Details:

- Schedule: Runs daily at 00:00 UTC (09:00 JST) via cron (commented out by default); to enable automatic scheduling, uncomment the `schedule` section in `.github/workflows/post_papers.yml`.
- Dispatch: Can be triggered manually via the "Run workflow" button.
- Steps:
  1. Checkout the repository.
  2. Setup Python 3.12.
  3. Install dependencies.
  4. Generate `secrets.yaml` from GitHub repository secrets.
  5. Run `python main.py`.

### GitHub Repository Secrets (Environment Variables)

The workflow uses GitHub repository secrets as environment variables (accessible in Actions via `${{ secrets.<NAME> }}`).
Configure them in your repository settings under **Settings > Secrets > Actions**:

- `OPENAI_API_KEY`: Your OpenAI API key.
- `SLACK_API_TOKEN_<WORKSPACE_NAME>`: Slack Bot User OAuth Token for each workspace defined in `config.yaml`. Replace `<WORKSPACE_NAME>` with the `name` field.
- For additional workspaces, add matching secrets and update the `slack_api_tokens` mapping in `.github/workflows/post_papers.yml`.