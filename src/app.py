from flask import Flask, render_template, request
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import certifi
import cloudscraper
import os
import urllib3

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 创建requests会话，设置用户代理和SSL验证
session = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'mobile': False,
})
session.verify = certifi.where()
app = Flask(__name__, template_folder='../templates')


def load_qoj_cookie():
    cookie = os.environ.get('QOJ_COOKIE', '').strip()
    cookie_file = os.environ.get('QOJ_COOKIE_FILE', '').strip()
    if not cookie and cookie_file:
        try:
            with open(cookie_file, encoding='utf-8') as file:
                cookie = file.read().strip()
        except OSError:
            return
    if cookie:
        session.headers.update({'Cookie': cookie})


load_qoj_cookie()


def is_login_page(response):
    return '/login' in response.url or '<title>Login - QOJ.ac</title>' in response.text


def get_cell_text(cell):
    return ' '.join(cell.get_text(' ', strip=True).split())


def classify_submission(result):
    lower_result = result.lower()
    if 'accepted' in lower_result or lower_result == 'ac':
        return 'accepted'
    if 'compiling' in lower_result or 'judging' in lower_result or 'waiting' in lower_result:
        return 'pending'
    if not result or result == '-':
        return 'unknown'
    return 'failed'


def parse_submission_table(html):
    soup = BeautifulSoup(html, 'html.parser')
    submissions = []
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if not rows:
            continue
        headers = [get_cell_text(cell).lower() for cell in rows[0].find_all(['th', 'td'])]
        header_text = ' '.join(headers)
        if 'submit' not in header_text and 'result' not in header_text and 'score' not in header_text:
            continue
        for row in rows[1:]:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue
            values = [get_cell_text(cell) for cell in cells]
            links = [
                link.get('href', '')
                for cell in cells
                for link in cell.find_all('a', href=True)
            ]
            submission = {
                'id': values[0],
                'problem': values[1] if len(values) > 1 else '',
                'result': '',
                'time': '',
                'language': '',
                'class': 'unknown',
                'url': next((href for href in links if '/submission/' in href), ''),
            }
            for value in values:
                lower_value = value.lower()
                if any(word in lower_value for word in ['accepted', 'wrong answer', 'time limit', 'memory limit', 'runtime error', 'compile error', 'compiling', 'judging', 'waiting']):
                    submission['result'] = value
                    break
            if not submission['result'] and len(values) >= 3:
                submission['result'] = values[2]
            for value in reversed(values):
                if ':' in value or '-' in value:
                    submission['time'] = value
                    break
            for value in values:
                lower_value = value.lower()
                if any(lang in lower_value for lang in ['c++', 'python', 'java', 'rust', 'go', 'kotlin', 'c#']):
                    submission['language'] = value
                    break
            submission['class'] = classify_submission(submission['result'])
            submissions.append(submission)
    return submissions


def get_submissions(contestid, player, limit=18):
    urls = [
        f'https://qoj.ac/contest/{contestid}/submissions?submitter={player}',
        f'https://qoj.ac/submissions?contest=QOJ{contestid}&submitter={player}',
        f'https://qoj.ac/submissions?contest_id={contestid}&submitter={player}',
        f'https://qoj.ac/submissions?submitter={player}',
    ]
    for url in urls:
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
        except Exception:
            continue
        if is_login_page(response):
            return {
                'error': 'Submissions require QOJ login cookie',
                'items': [],
            }
        submissions = parse_submission_table(response.text)
        if submissions:
            return {
                'items': submissions[:limit],
                'source': url,
            }
    return {
        'items': [],
        'error': 'No submissions found',
    }


def get_contest_info(contestid, player):
    contest = {'problems': []}
    try:
        response = session.get(
            f'https://qoj.ac/results/QOJ{contestid}?player={player}',
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        contest['error'] = f'Failed to load QOJ: {exc}'
        return contest

    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.find('title')
    if title and 'Just a moment' in title.text:
        contest['error'] = 'QOJ returned a Cloudflare challenge'
        return contest

    h2 = soup.find('h2')
    if h2 is not None:
        contest['name'] = h2.text.strip()
    for p in soup.find_all('p'):
        txt = p.text.strip()
        if txt.startswith('Current Time: '):
            current_time = txt[len('Current Time: '):].strip()  # e.g., '2:33:13'
            # Parse current_time as timedelta and subtract from now
            h, m, s = map(int, current_time.split(':'))
            delta = timedelta(hours=h, minutes=m, seconds=s)
            start_time = datetime.now() - delta
            end_time = start_time + timedelta(hours=5)
            contest['start_time'] = start_time.strftime('%Y-%m-%d %H:%M:%S')
            contest['end_time'] = end_time.strftime('%Y-%m-%d %H:%M:%S')

    headers = soup.find_all('th')
    mystanding = soup.find('tr', class_='solver')
    player_key = player.strip().lower()
    if mystanding is None and player_key:
        for row in soup.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue
            username_cell = cells[1]
            username_text = username_cell.get_text(' ', strip=True).lower()
            profile_links = [
                link.get('href', '').rstrip('/').split('/')[-1].lower()
                for link in username_cell.find_all('a', href=True)
            ]
            if player_key == username_text or player_key in profile_links:
                mystanding = row
                break

    if mystanding is not None:
        myrow = mystanding.find_all('td')
        problems = []
        last_problem_column = min(len(headers), len(myrow)) - 3
        for i in range(2, last_problem_column):
            problem_index = headers[i].get_text(' ', strip=True).split()[0]
            mystatus = myrow[i].get('class', ['stnd'])[0]
            mytag = myrow[i].get_text(' ', strip=True)
            problems.append(
                {
                    'index': problem_index,
                    'status': mystatus,
                    'tag': mytag
                }
            )
        solved = myrow[-3].text.strip()
        penalty = myrow[-2].text.strip()
        rank = myrow[0].text.strip()
        contest['rank'] = rank
        contest['problems'] = problems
        contest['solved'] = solved
        contest['penalty'] = penalty
    elif contest.get('name'):
        contest['error'] = f'Player "{player}" was not found in this standings page'
    contest['submissions'] = get_submissions(contestid, player)
    return contest

@app.route('/overlay', methods=['GET'])
def overlay():
    if 'contest' not in request.args or 'player' not in request.args:
        return render_template('overlay.html', contest=None)
    contest = get_contest_info(request.args['contest'], request.args['player'])
    return render_template('overlay.html',
                           contest=contest,
                            player=request.args['player'],
                            contest_id=request.args['contest'])

@app.route('/overlay-update', methods=['GET'])
def overlay_update():
    if 'contest' not in request.args or 'player' not in request.args:
        return render_template('overlay_update.html', contest=None)
    contest = get_contest_info(request.args['contest'], request.args['player'])
    return render_template('overlay_update.html',
                           contest=contest,
                           player=request.args['player'],
                           contest_id=request.args['contest'])


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
