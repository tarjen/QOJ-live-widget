# QOJ Live Widget

## Installation

```bash
pip install bs4 flask cloudscraper
```

## Usage

```bash
python src/app.py
```

Then access the widget at `http://[your ip or localhost]:5000/overlay?contest=[contest_id]&player=[player_id]`.

The right-hand panel shows the latest submission for each problem, sorted by
the most recent submission. If QOJ redirects submissions to login, pass your
browser cookie before starting the app:

```bash
$env:QOJ_COOKIE="key=value; another_key=another_value"
python src/app.py
```

Or keep the cookie in a local file:

```bash
$env:QOJ_COOKIE_FILE="C:\path\to\qoj-cookie.txt"
python src/app.py
```
