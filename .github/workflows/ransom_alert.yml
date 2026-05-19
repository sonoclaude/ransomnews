name: Ransom Alert Italia

on:
  schedule:
    - cron: '*/30 * * * *'   # ogni 30 minuti — check rivendicazioni
    - cron: '30 5 * * *'     # 07:30 ora italiana (05:30 UTC) — ping mattutino
    - cron: '30 18 * * *'    # 20:30 ora italiana (18:30 UTC) — ping serale
  workflow_dispatch:          # avvio manuale da GitHub

jobs:
  check:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests beautifulsoup4

      - name: Run ransom alert
        env:
          TELEGRAM_TOKEN:   ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          GITHUB_EVENT_SCHEDULE: ${{ github.event.schedule }}
        run: python ransom_alert/ransom_alert.py

      - name: Salva seen.json nel repository
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add ransom_alert/seen.json
          git diff --cached --quiet || git commit -m "Auto: aggiorna seen.json [skip ci]"
          git pull --rebase origin main
          git push
